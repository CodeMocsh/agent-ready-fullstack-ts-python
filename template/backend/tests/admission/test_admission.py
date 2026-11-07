"""Admission: each actor gets a bounded number of requests at once, at a bounded rate, and a
bounded number of streams, in each process. A request over a bound is refused with a `429` and
a `Retry-After`, and records no request event. `docs/adr/template/0023`."""

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Annotated, Any

import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import StreamingResponse
from starlette.types import Message

from app import admission, log
from app.admission import Admission, Limits, Policy
from app.admission_limits import LIMITS
from app.audit_events import AuditEventPage, RequestEvent
from app.deployment import ADMISSION_IN_FLIGHT_ENV, ADMISSION_MODE_ENV, ADMISSION_STREAMS_ENV
from app.deps import admitted
from app.identity.shared import Actor, Principal
from app.refusal import SAID_OF_EACH_BOUND, TooManyRequests
from app.store import AuditEvents, Database
from tests.conftest import Logged
from tests.pages import every_event
from tests.routed import assembled

WHO: str = "x-test-actor"
TENANT = "tenant-a"


def an_actor(subject: str) -> Actor:
    return Actor(issuer="a-test", subject=subject, name=subject)


def a_principal(subject: str) -> Principal:
    return Principal(tenant_id=TENANT, actor=an_actor(subject))


def enforcing(limits: Limits) -> Policy:
    return Policy(limits=limits, enforced=True)


class Clock:
    def __init__(self) -> None:
        self.now: float = 100.0

    def __call__(self) -> float:
        return self.now


def test_an_actor_at_its_requests_in_flight_is_refused_until_one_ends() -> None:
    places = Admission(enforcing(Limits(in_flight=2, per_second=100, burst=100, streams=1)))
    alice = an_actor("alice")
    first = places.admit(alice)
    _ = places.admit(alice)

    with pytest.raises(TooManyRequests) as refused:
        places.admit(alice)
    first()

    _ = places.admit(alice)
    assert (refused.value.status_code, refused.value.headers) == (429, {"Retry-After": "1"})


def test_an_actor_that_spent_its_burst_waits_for_its_rate() -> None:
    clock = Clock()
    places = Admission(enforcing(Limits(in_flight=10, per_second=0.5, burst=3, streams=1)), clock)
    alice = an_actor("alice")
    for _ in range(3):
        places.admit(alice)()

    with pytest.raises(TooManyRequests) as spent:
        places.admit(alice)
    clock.now += 1.0
    with pytest.raises(TooManyRequests) as half:
        places.admit(alice)
    clock.now += 1.0

    places.admit(alice)()
    assert (spent.value.headers, half.value.headers) == ({"Retry-After": "2"}, {"Retry-After": "1"})


def test_one_actor_over_its_bound_leaves_every_other_actor_admitted() -> None:
    places = Admission(enforcing(Limits(in_flight=1, per_second=100, burst=100, streams=1)))
    _ = places.admit(an_actor("alice"))

    with pytest.raises(TooManyRequests):
        places.admit(an_actor("alice"))
    _ = places.admit(an_actor("bob"))


def test_streams_have_their_own_bound_beside_the_requests() -> None:
    places = Admission(enforcing(Limits(in_flight=1, per_second=100, burst=100, streams=1)))
    alice = an_actor("alice")
    _ = places.admit(alice)
    stream = places.stream(alice)

    with pytest.raises(TooManyRequests):
        places.stream(alice)
    stream()

    _ = places.stream(alice)


def test_a_place_given_back_twice_is_given_back_once() -> None:
    places = Admission(enforcing(Limits(in_flight=1, per_second=100, burst=100, streams=1)))
    alice = an_actor("alice")
    release = places.admit(alice)
    release()
    release()
    _ = places.admit(alice)

    with pytest.raises(TooManyRequests):
        places.admit(alice)


def test_an_actor_that_holds_nothing_and_has_its_burst_back_is_forgotten() -> None:
    clock = Clock()
    places = Admission(enforcing(Limits(in_flight=1, per_second=1, burst=2, streams=1)), clock)
    places.admit(an_actor("alice"))()
    clock.now += 2.0

    places.admit(an_actor("bob"))()

    assert places.actors() == 1


@pytest.mark.parametrize(
    "limits",
    [
        lambda: Limits(in_flight=0, per_second=1, burst=1, streams=1),
        lambda: Limits(in_flight=1, per_second=0, burst=1, streams=1),
        lambda: Limits(in_flight=1, per_second=float("inf"), burst=1, streams=1),
        lambda: Limits(in_flight=1, per_second=1, burst=0, streams=1),
        lambda: Limits(in_flight=1, per_second=1, burst=1, streams=0),
    ],
    ids=["no-request", "no-rate", "no-bound-on-the-rate", "no-burst", "no-stream"],
)
def test_limits_that_admit_nothing_are_refused(limits: Callable[[], Limits]) -> None:
    with pytest.raises(ValueError, match="admits nothing"):
        limits()


async def admitted_from_header(request: Request) -> Principal:
    """What `resolved_principal` does, with a principal read from a header of the test's own."""
    return admitted(request, a_principal(request.headers[WHO]))


class Held:
    """A route that holds each request until `go` is set, and a stream that stays open until
    then. `entered` counts the requests each route began to serve."""

    def __init__(self) -> None:
        self.go: asyncio.Event = asyncio.Event()
        self.count: int = 0
        self.router: APIRouter = APIRouter(dependencies=[Depends(admitted_from_header)])

        @self.router.get("/held")
        async def held() -> dict[str, str]:
            self.count += 1
            await self.go.wait()
            return {"held": "done"}

        @self.router.get("/stream")
        async def stream(
            request: Request, principal: Annotated[Principal, Depends(admitted_from_header)]
        ) -> StreamingResponse:
            admission.stream(request, principal.actor)
            self.count += 1

            async def body() -> AsyncIterator[bytes]:
                yield b"open"
                await self.go.wait()

            return StreamingResponse(body())

    async def entered(self, count: int) -> None:
        async with asyncio.timeout(5):
            while self.count < count:
                await asyncio.sleep(0)


def an_app(database: Database, held: Held) -> FastAPI:
    return assembled(held.router, database=database)


async def called(app: FastAPI, path: str, subject: str) -> list[Message]:
    """What `app` sent for one GET of `path` by `subject`, once the response ends."""
    sent: list[Message] = []
    asked = False
    never = asyncio.Event()

    async def receive() -> Message:
        nonlocal asked
        if not asked:
            asked = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await never.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(WHO.encode(), subject.encode())],
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
    }
    await app(scope, receive, send)
    return sent


async def refused_within(app: FastAPI, path: str, subject: str) -> list[Message]:
    """What `app` sent for a GET it must refuse at once, rather than hold."""
    async with asyncio.timeout(5):
        return await called(app, path, subject)


def status_of(sent: list[Message]) -> int:
    return next(one["status"] for one in sent if one["type"] == "http.response.start")


def retry_after_of(sent: list[Message]) -> bytes:
    start = next(one for one in sent if one["type"] == "http.response.start")
    return dict(start["headers"])[b"retry-after"]


async def test_a_request_over_the_bound_is_refused_and_records_no_request_event(
    database: Database,
) -> None:
    held = Held()
    app = an_app(database, held)
    async with asyncio.TaskGroup() as group:
        holding = [
            group.create_task(called(app, "/held", "alice")) for _ in range(LIMITS.in_flight)
        ]
        await held.entered(LIMITS.in_flight)
        refused = await refused_within(app, "/held", "alice")
        other = group.create_task(called(app, "/held", "bob"))
        await held.entered(LIMITS.in_flight + 1)
        held.go.set()
    recorded = await every_event(database.audit_events(TENANT))

    assert (status_of(refused), retry_after_of(refused)) == (429, b"1")
    assert [status_of(task.result()) for task in [*holding, other]] == [200] * (
        LIMITS.in_flight + 1
    )
    assert len(recorded) == LIMITS.in_flight + 1


class RecordingHeld:
    """The substrate the app serves from, except that each request event waits for `go`."""

    def __init__(self, database: Database) -> None:
        self._database: Database = database
        self.go: asyncio.Event = asyncio.Event()
        self.waiting: int = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._database, name)

    def audit_events(self, tenant_id: str) -> "HeldEvents":
        return HeldEvents(self, self._database.audit_events(tenant_id))


class HeldEvents:
    def __init__(self, held: RecordingHeld, events: AuditEvents) -> None:
        self._held: RecordingHeld = held
        self._events: AuditEvents = events

    async def record(self, event: RequestEvent) -> None:
        self._held.waiting += 1
        await self._held.go.wait()
        await self._events.record(event)

    async def list(self, after: str | None) -> AuditEventPage:
        return await self._events.list(after)


async def test_a_request_holds_its_place_until_its_request_event_is_recorded(
    database: Database,
) -> None:
    held = Held()
    held.go.set()
    app = an_app(database, held)
    recording = RecordingHeld(database)
    app.state.database = recording
    async with asyncio.TaskGroup() as group:
        holding = [
            group.create_task(called(app, "/held", "alice")) for _ in range(LIMITS.in_flight)
        ]
        async with asyncio.timeout(5):
            while recording.waiting < LIMITS.in_flight:
                await asyncio.sleep(0)
        refused = await refused_within(app, "/held", "alice")
        recording.go.set()

    assert status_of(refused) == 429
    assert [status_of(task.result()) for task in holding] == [200] * LIMITS.in_flight


async def test_an_open_stream_holds_a_stream_place_and_no_request_place(
    database: Database,
) -> None:
    held = Held()
    app = an_app(database, held)
    async with asyncio.TaskGroup() as group:
        streams = [
            group.create_task(called(app, "/stream", "alice")) for _ in range(LIMITS.streams)
        ]
        await held.entered(LIMITS.streams)
        refused = await refused_within(app, "/stream", "alice")
        requests = [
            group.create_task(called(app, "/held", "alice")) for _ in range(LIMITS.in_flight)
        ]
        await held.entered(LIMITS.streams + LIMITS.in_flight)
        held.go.set()

    recorded = await every_event(database.audit_events(TENANT))

    assert status_of(refused) == 429
    assert [status_of(task.result()) for task in [*streams, *requests]] == [200] * (
        LIMITS.streams + LIMITS.in_flight
    )
    assert len(recorded) == LIMITS.streams + LIMITS.in_flight


async def admitted_twice_from_header(request: Request) -> Principal:
    """What a project's resolver that admits where it verifies the credential does, before
    `resolved_principal` admits the same request again."""
    principal = a_principal(request.headers[WHO])
    admission.admit(request, principal.actor)
    return admitted(request, principal)


async def test_a_request_admitted_twice_holds_one_place(database: Database) -> None:
    router = APIRouter(dependencies=[Depends(admitted_twice_from_header)])

    @router.get("/twice")
    async def twice() -> dict[str, str]:
        return {"twice": "done"}

    app = assembled(router, database=database)
    answered = [await called(app, "/twice", "alice") for _ in range(LIMITS.in_flight + 1)]

    assert [status_of(sent) for sent in answered] == [200] * (LIMITS.in_flight + 1)


@pytest.mark.parametrize(
    ("limits", "bound"),
    [
        (Limits(in_flight=1, per_second=100, burst=100, streams=1), "in_flight"),
        (Limits(in_flight=10, per_second=0.5, burst=1, streams=1), "rate"),
        (Limits(in_flight=10, per_second=100, burst=100, streams=1), "streams"),
    ],
    ids=["in-flight", "rate", "streams"],
)
def test_a_refusal_names_the_bound_that_refused_it(
    logged: Logged, limits: Limits, bound: str
) -> None:
    log.configure(None)
    places = Admission(enforcing(limits), Clock())
    take = places.stream if bound == "streams" else places.admit
    _ = take(an_actor("alice"))

    with pytest.raises(TooManyRequests) as refused:
        take(an_actor("alice"))

    assert refused.value.bound == bound
    assert SAID_OF_EACH_BOUND[refused.value.bound] in refused.value.detail
    assert [(one["message"], one["bound"]) for one in logged() if "bound" in one] == [
        ("admission refused", bound)
    ]


def test_a_policy_that_evaluates_reports_what_an_enforced_policy_refuses(logged: Logged) -> None:
    log.configure(None)
    limits = Limits(in_flight=10, per_second=1, burst=3, streams=1)
    clock = Clock()
    enforced = Admission(enforcing(limits), clock)
    evaluated = Admission(Policy(limits=limits, enforced=False), clock)
    refused: list[bool] = []
    for step in range(30):
        clock.now += 0.2 if step < 20 else 1.5
        try:
            enforced.admit(an_actor("alice"))()
            refused.append(False)
        except TooManyRequests:
            refused.append(True)
        evaluated.admit(an_actor("alice"))()

    reported = [one for one in logged() if one["message"] == "admission would refuse"]

    assert len(reported) == sum(refused)
    assert refused[-10:] == [False] * 10


def test_a_policy_that_evaluates_admits_over_a_bound_and_names_it(logged: Logged) -> None:
    log.configure(None)
    limits = Limits(in_flight=1, per_second=100, burst=100, streams=1)
    places = Admission(Policy(limits=limits, enforced=False))
    alice = an_actor("alice")
    _ = places.admit(alice)
    _ = places.admit(alice)
    _ = places.stream(alice)
    _ = places.stream(alice)

    assert [(one["message"], one["bound"]) for one in logged() if "bound" in one] == [
        ("admission would refuse", "in_flight"),
        ("admission would refuse", "streams"),
    ]


def test_a_policy_that_evaluates_counts_places_as_an_enforced_policy_does(logged: Logged) -> None:
    log.configure(None)
    limits = Limits(in_flight=2, per_second=100, burst=100, streams=1)
    enforced = Admission(enforcing(limits))
    evaluated = Admission(Policy(limits=limits, enforced=False))

    def steps(places: Admission) -> list[str]:
        alice = an_actor("alice")
        held: list[Callable[[], None]] = []
        outcomes: list[str] = []
        for _ in range(3):
            try:
                held.append(places.admit(alice))
                outcomes.append("admitted")
            except TooManyRequests:
                outcomes.append("refused")
        held[0]()
        try:
            held.append(places.admit(alice))
            outcomes.append("admitted")
        except TooManyRequests:
            outcomes.append("refused")
        return outcomes

    refused = steps(enforced)
    _ = logged()
    _ = steps(evaluated)
    reported = [one["bound"] for one in logged() if one["message"] == "admission would refuse"]

    assert refused == ["admitted", "admitted", "refused", "admitted"]
    assert reported == ["in_flight"]


async def test_a_stream_of_a_request_admitted_outside_the_count_is_outside_it_too(
    database: Database, monkeypatch: pytest.MonkeyPatch, logged: Logged
) -> None:
    monkeypatch.setenv(ADMISSION_MODE_ENV, "evaluate")
    monkeypatch.setenv(ADMISSION_IN_FLIGHT_ENV, "1")
    monkeypatch.setenv(ADMISSION_STREAMS_ENV, "1")
    held = Held()
    app = an_app(database, held)
    async with asyncio.TaskGroup() as group:
        _ = group.create_task(called(app, "/held", "alice"))
        await held.entered(1)
        _ = group.create_task(called(app, "/stream", "alice"))
        await held.entered(2)
        _ = group.create_task(called(app, "/stream", "alice"))
        await held.entered(3)
        held.go.set()

    reported = [one["bound"] for one in logged() if one["message"] == "admission would refuse"]

    assert reported == ["in_flight", "in_flight"]
