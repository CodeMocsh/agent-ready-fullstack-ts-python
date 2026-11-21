"""A GET request reads every read on one connection of one snapshot, and each answer once.
`docs/adr/template/0033`."""

import asyncio
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.pacing import yield_now
from app.store import snapshots
from app.store.loads import loader_of, loading
from app.store.snapshots import ReadInsideARead, reading, snapshotted, written
from app.waits import SharedWork, beside


class Unanswerable(LookupError):
    pass


class NotFound(LookupError):
    pass


@dataclass
class Units:
    """Each unit opened, each statement sent in it, how each ended, and the most open at once."""

    opened: int = 0
    open: int = 0
    most_open: int = 0
    sent: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)
    ended: list[str] = field(default_factory=list)
    released: asyncio.Event = field(default_factory=asyncio.Event)

    @asynccontextmanager
    async def unit(self) -> AsyncGenerator["Units"]:
        self.opened += 1
        self.open += 1
        self.most_open = max(self.most_open, self.open)
        try:
            yield self
        except BaseException as failed:
            self.ended.append(type(failed).__name__)
            raise
        else:
            self.ended.append("committed")
        finally:
            self.open -= 1

    async def fetch(self, query: str, *args: Any) -> list[Any]:
        self.sent.append((query, args))
        if query == "unanswerable":
            raise Unanswerable(query)
        if query == "held":
            await self.released.wait()
        return [query, *args]

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.sent.append((query, args))
        return len(self.sent)


def read(units: Units, alone: Units | None = None, whose: str = "t") -> Any:
    return reading(whose, units.unit, (alone or Units()).unit, (Unanswerable,))


async def fetched(units: Units, query: str, *args: Any, alone: Units | None = None) -> Any:
    async with read(units, alone) as conn:
        return await conn.fetch(query, *args)


async def test_a_scope_reads_on_one_unit_and_sends_each_statement_once() -> None:
    units = Units()
    async with snapshotted():
        first = await fetched(units, "a", 1)
        again = await fetched(units, "a", 1)
        other = await fetched(units, "a", 2)

    assert (first, again, other) == (["a", 1], ["a", 1], ["a", 2])
    assert units.sent == [("a", (1,)), ("a", (2,))]
    assert units.opened == 1
    assert units.ended == ["committed"]


async def test_a_kept_list_of_rows_is_handed_out_as_a_copy() -> None:
    units = Units()
    async with snapshotted():
        first = await fetched(units, "a")
        first.append("changed")
        assert await fetched(units, "a") == ["a"]


async def test_reads_at_once_take_turns_on_one_unit() -> None:
    units = Units()
    async with snapshotted(), asyncio.TaskGroup() as group:
        for n in range(5):
            _ = group.create_task(fetched(units, "a", n))

    assert units.opened == 1
    assert units.most_open == 1
    assert len(units.sent) == 5


async def test_outside_a_scope_each_read_is_a_unit_of_its_own() -> None:
    units, alone = Units(), Units()
    _ = await fetched(units, "a", alone=alone)
    _ = await fetched(units, "a", alone=alone)

    assert units.opened == 0
    assert alone.opened == 2
    assert alone.sent == [("a", ()), ("a", ())]


async def test_after_a_write_the_next_read_opens_a_new_snapshot_that_keeps_nothing() -> None:
    units = Units()
    async with snapshotted():
        _ = await fetched(units, "a")
        await written("t")
        _ = await fetched(units, "a")

    assert units.opened == 2
    assert units.sent == [("a", ()), ("a", ())]


async def test_a_write_ends_only_the_snapshot_it_wrote_to() -> None:
    units = Units()
    async with snapshotted():
        _ = await fetched(units, "a")
        await written("u")
        _ = await fetched(units, "a")

    assert units.opened == 1


async def test_a_read_that_fails_raises_in_its_caller_and_the_next_read_opens_a_new_one() -> None:
    units = Units()
    async with snapshotted():
        _ = await fetched(units, "a")
        with pytest.raises(Unanswerable):
            _ = await fetched(units, "unanswerable")
        assert await fetched(units, "a") == ["a"]

    assert units.ended == ["Unanswerable", "committed"]
    assert units.opened == 2
    assert units.sent[-1] == ("a", ())


async def test_a_read_inside_a_read_is_refused() -> None:
    units = Units()
    async with snapshotted(), read(units):
        with pytest.raises(ReadInsideARead, match="inside a read of a snapshot"):
            async with asyncio.timeout(1), read(units):
                pass


async def test_a_task_that_a_read_started_is_refused_a_read() -> None:
    units = Units()
    async with snapshotted(), read(units):
        task = asyncio.get_running_loop().create_task(fetched(units, "a"))
        with pytest.raises(ReadInsideARead):
            async with asyncio.timeout(1):
                await task


async def test_work_beside_the_requests_reads_alone() -> None:
    units, alone = Units(), Units()
    async with snapshotted():
        _ = await beside(fetched(units, "a", alone=alone), name="reads beside", within=1)

    assert units.opened == 0
    assert alone.opened == 1


async def test_after_the_scope_ends_a_read_is_alone() -> None:
    units, alone = Units(), Units()
    async with snapshotted() as scope:
        _ = await fetched(units, "a", alone=alone)
        await scope.end()
        _ = await fetched(units, "a", alone=alone)

    assert units.opened == 1
    assert alone.opened == 1


async def test_the_end_of_a_snapshot_waits_for_the_read_in_flight() -> None:
    units = Units()
    async with snapshotted() as scope:
        reads = asyncio.get_running_loop().create_task(fetched(units, "held"))
        while not units.sent:
            await yield_now()
        ends = asyncio.get_running_loop().create_task(scope.end())
        await yield_now()
        assert not ends.done()
        units.released.set()
        await ends
        assert await reads == ["held"]

    assert units.ended == ["committed"]


async def test_an_argument_that_cannot_be_kept_is_refused() -> None:
    units = Units()
    async with snapshotted():
        with pytest.raises(TypeError, match="cannot be kept"):
            _ = await fetched(units, "a", bytearray(b"not keepable"))


async def test_an_argument_is_kept_by_its_type_as_well_as_its_value() -> None:
    units = Units()
    async with snapshotted():
        _ = await fetched(units, "a", 1)
        _ = await fetched(units, "a", True)
        _ = await fetched(units, "a", [1, 2])
        _ = await fetched(units, "a", (1, 2))
        _ = await fetched(units, "a", {"b": [1]})
        _ = await fetched(units, "a", {"b": [1]})
        _ = await fetched(units, "a", {1, 2})
        _ = await fetched(units, "a", {2, 1})

    assert len(units.sent) == 6


class OpenAtTheStart:
    """Records how many units were open when each response started."""

    def __init__(self, app: ASGIApp, units: Units, seen: list[int]) -> None:
        self._app: ASGIApp = app
        self._units: Units = units
        self._seen: list[int] = seen

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def started(message: Message) -> None:
            if message["type"] == "http.response.start":
                self._seen.append(self._units.open)
            await send(message)

        await self._app(scope, receive, started)


def test_a_get_request_ends_its_snapshot_before_its_response_starts() -> None:
    units, alone = Units(), Units()
    seen: list[int] = []
    app = FastAPI()
    snapshots.instrument(app)
    app.add_middleware(OpenAtTheStart, units=units, seen=seen)

    @app.api_route("/read", methods=["GET", "POST"])
    async def route() -> int:
        _ = await fetched(units, "a", alone=alone)
        _ = await fetched(units, "a", alone=alone)
        return units.open

    with TestClient(app) as client:
        assert client.get("/read").json() == 1
        assert client.post("/read").json() == 0

    assert seen == [0, 0]
    assert units.opened == 1
    assert alone.opened == 2


async def test_a_read_in_flight_ends_the_snapshot_when_its_end_is_cancelled() -> None:
    units = Units()
    async with snapshotted() as scope:
        reads = asyncio.get_running_loop().create_task(fetched(units, "held"))
        while not units.sent:
            await yield_now()
        ends = asyncio.get_running_loop().create_task(scope.end())
        await yield_now()
        _ = ends.cancel()
        units.released.set()
        assert await reads == ["held"]
        with pytest.raises(asyncio.CancelledError):
            await ends
        assert units.ended == ["committed"]
        assert units.open == 0


async def test_a_statement_whose_answer_changes_in_a_transaction_is_sent_each_time() -> None:
    units = Units()
    async with snapshotted():
        _ = await fetched(units, "SELECT clock_timestamp()")
        _ = await fetched(units, "SELECT clock_timestamp()")
        _ = await fetched(units, "SELECT now()")
        _ = await fetched(units, "SELECT now()")

    assert [query for query, _ in units.sent] == [
        "SELECT clock_timestamp()",
        "SELECT clock_timestamp()",
        "SELECT now()",
    ]


async def test_a_failure_that_is_not_the_database_s_keeps_the_snapshot() -> None:
    units = Units()
    async with snapshotted():
        with pytest.raises(NotFound):
            async with read(units) as conn:
                _ = await conn.fetch("a")
                raise NotFound("a")
        _ = await fetched(units, "a")

    assert units.opened == 1
    assert units.sent == [("a", ())]


async def test_a_write_inside_a_read_ends_the_snapshot_when_the_read_ends() -> None:
    units = Units()
    async with snapshotted():
        async with asyncio.timeout(1), read(units) as conn:
            _ = await conn.fetch("a")
            await written("t")
            assert units.open == 1
        assert units.open == 0
        _ = await fetched(units, "a")

    assert units.opened == 2


async def test_a_wait_for_a_turn_of_a_loader_inside_a_read_is_refused() -> None:
    units = Units()

    async def doubled(_conn: Any, keys: Sequence[int]) -> Mapping[int, int]:
        return {key: key * 2 for key in keys}

    with loading():
        async with snapshotted(), read(units):
            with pytest.raises(ReadInsideARead, match="a wait for a turn"):
                async with asyncio.timeout(1):
                    _ = await loader_of("t", units.unit).load(doubled, 1)


async def test_a_turn_of_a_loader_reads_on_the_snapshot_and_other_shared_work_reads_alone() -> None:
    units, alone = Units(), Units()

    async def keyed(conn: Any, keys: Sequence[int]) -> Mapping[int, Any]:
        return {key: await conn.fetch("keyed", key) for key in keys}

    shared = SharedWork[str, Any]("reads in shared work")
    with loading():
        async with snapshotted():
            loader = loader_of("t", lambda: read(units, alone))
            assert await loader.load(keyed, 1) == ["keyed", 1]
            _ = await shared.joined("a", lambda: fetched(units, "a", alone=alone))

    assert units.sent == [("keyed", (1,))]
    assert alone.sent == [("a", ())]


async def test_each_snapshot_ends_when_another_fails_to_end() -> None:
    failing, units = Units(), Units()

    @asynccontextmanager
    async def fails_to_end() -> AsyncGenerator[Units]:
        async with failing.unit() as conn:
            yield conn
        raise Unanswerable("the end")

    async with snapshotted() as scope:
        async with reading("first", fails_to_end, Units().unit, ()) as conn:
            _ = await conn.fetch("a")
        _ = await fetched(units, "a")
        with pytest.raises(Unanswerable, match="the end"):
            await scope.end()
        assert units.ended == ["committed"]


class EndsAround:
    """Records how many units were open when each request ended, as the request event does."""

    def __init__(self, app: ASGIApp, units: Units, seen: list[int]) -> None:
        self._app: ASGIApp = app
        self._units: Units = units
        self._seen: list[int] = seen

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        try:
            await self._app(scope, receive, send)
        finally:
            self._seen.append(self._units.open)


def test_each_get_and_head_request_ends_its_snapshot_before_its_request_event() -> None:
    units = Units()
    seen: list[int] = []
    app = FastAPI()
    snapshots.instrument(app)
    app.add_middleware(EndsAround, units=units, seen=seen)

    @app.api_route("/fails", methods=["GET", "HEAD"])
    async def fails() -> None:
        _ = await fetched(units, "a")
        raise RuntimeError("the route failed")

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/fails").status_code == 500
        assert client.head("/fails").status_code == 500

    assert seen == [0, 0]
    assert units.opened == 2


async def test_a_write_inside_a_read_that_then_fails_ends_the_snapshot() -> None:
    units = Units()
    async with snapshotted():
        with pytest.raises(NotFound):
            async with read(units) as conn:
                _ = await conn.fetch("a")
                await written("t")
                raise NotFound("a")
        _ = await fetched(units, "a")

    assert units.opened == 2
    assert units.sent == [("a", ()), ("a", ())]


async def test_a_task_that_a_read_started_and_that_writes_after_the_read_ends_the_snapshot() -> (
    None
):
    units = Units()
    async with snapshotted():
        async with read(units) as conn:
            _ = await conn.fetch("a")
            writes = asyncio.get_running_loop().create_task(written("t"))
        await writes
        assert units.open == 0
        _ = await fetched(units, "a")

    assert units.opened == 2
