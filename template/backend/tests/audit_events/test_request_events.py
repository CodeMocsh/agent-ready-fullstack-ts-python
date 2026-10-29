"""The request event: one for each request a principal made, recorded before the response leaves.

Driven through a real `TestClient` on the in-memory substrate, with a dependency that serves a
principal through `audit_events.serve` as the identity dependency does, in front of the tests'
own widgets. Nothing here needs the example resource or the identity stub.
"""

import asyncio
from typing import Annotated, Any

import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from app.audit_events import (
    SERVED,
    RecordedAuditEvent,
    RequestEvent,
    ServedOffTheLoop,
    current_actor,
    serve,
)
from app.identity.shared import Actor, Principal, Unauthenticated
from app.main import ROUTERS
from app.request_events import RecordsRequests
from app.store import Database
from tests.routed import assembled
from tests.widgets import router as widgets_router

TENANT_HEADER = "x-test-tenant"
PERSON = Actor(issuer="a-test", subject="a-person", name="A person")
NESTED = Actor(
    issuer="a-test",
    subject="a-sub-agent",
    name="A sub-agent",
    on_behalf_of=Actor(issuer="a-test", subject="an-agent", name="An agent", on_behalf_of=PERSON),
)


def principal_from_header(request: Request) -> Principal:
    named = request.headers.get(TENANT_HEADER)
    if named is None:
        raise Unauthenticated("this request names no tenant")
    return Principal(tenant_id=named, actor=PERSON)


async def served(
    request: Request, principal: Annotated[Principal, Depends(principal_from_header)]
) -> Principal:
    return serve(request, principal)


def an_app(database: Database) -> FastAPI:
    """The app, also serving the widgets, `/raises` and `/acting` behind the identity
    dependency."""
    app = assembled(*ROUTERS, database=database)
    extra = APIRouter()

    @extra.get("/raises")
    async def raises() -> None:
        raise RuntimeError("the handler failed")

    @extra.get("/acting")
    async def acting() -> Actor | None:
        return current_actor()

    for router in (widgets_router, extra):
        app.include_router(router, dependencies=[Depends(served)])
    app.include_router(widgets_router, prefix="/shelves/{shelf}", dependencies=[Depends(served)])
    return app


def events_in(client: TestClient, app: FastAPI, tenant_id: str) -> list[RecordedAuditEvent]:
    assert client.portal is not None
    return client.portal.call(app.state.database.audit_events(tenant_id).list)


def test_each_request_a_principal_made_is_recorded_whatever_it_answered(database: Database) -> None:
    app = an_app(database)
    with TestClient(app, raise_server_exceptions=False) as client:
        client.get("/widgets", headers={TENANT_HEADER: "tenant-a"})
        client.post("/widgets", json={"name": "a"}, headers={TENANT_HEADER: "tenant-a"})
        client.get("/shelves/top/widgets/absent", headers={TENANT_HEADER: "tenant-a"})
        client.get("/raises", headers={TENANT_HEADER: "tenant-a"})
        client.get("/widgets", headers={TENANT_HEADER: "tenant-b"})
        recorded = events_in(client, app, "tenant-a")

    assert [(e.method, e.route, e.resource_ids, e.status, e.actor) for e in recorded] == [
        ("GET", "/widgets", (), 200, PERSON),
        ("POST", "/widgets", (), 201, PERSON),
        ("GET", "/shelves/{shelf}/widgets/{id}", ("top", "absent"), 404, PERSON),
        ("GET", "/raises", (), 500, PERSON),
    ]
    assert {(e.action, e.outcome, e.resource_type, e.fields) for e in recorded} == {
        (None, None, None, ())
    }


def test_a_request_event_names_the_request_its_response_carries(database: Database) -> None:
    app = an_app(database)
    with TestClient(app) as client:
        answered = client.get("/widgets", headers={TENANT_HEADER: "tenant-a"})
        [recorded] = events_in(client, app, "tenant-a")

    assert recorded.request_id == answered.headers["x-request-id"]


def test_a_request_with_no_principal_records_nothing(database: Database) -> None:
    """A request the seam refuses has no tenant to record in, and anybody can send one. A public
    route serves nobody."""
    app = an_app(database)
    with TestClient(app) as client:
        assert client.get("/widgets").status_code == 401
        assert client.get("/health").status_code == 200
        recorded = [
            *events_in(client, app, "tenant-a"),
            *events_in(client, app, "default"),
        ]

    assert recorded == []


def test_a_request_acts_as_its_principal(database: Database) -> None:
    with TestClient(an_app(database)) as client:
        answered = client.get("/acting", headers={TENANT_HEADER: "tenant-a"})

    assert Actor.model_validate(answered.json()) == PERSON


def test_an_actor_two_levels_deep_is_refused_and_records_nothing(database: Database) -> None:
    app = an_app(database)
    app.dependency_overrides[principal_from_header] = lambda: Principal(
        tenant_id="tenant-a", actor=NESTED
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/widgets", headers={TENANT_HEADER: "tenant-a"}).status_code == 500
        assert events_in(client, app, "tenant-a") == []


def test_a_principal_served_from_a_worker_thread_is_refused(database: Database) -> None:

    def served_in_a_thread(
        request: Request, principal: Annotated[Principal, Depends(principal_from_header)]
    ) -> Principal:
        return serve(request, principal)

    app = an_app(database)
    app.dependency_overrides[served] = served_in_a_thread
    with TestClient(app) as client, pytest.raises(ServedOffTheLoop):
        client.get("/acting", headers={TENANT_HEADER: "tenant-a"})


async def test_a_cancelled_request_records_nothing() -> None:
    """Recording would reach for the app and the substrate, which this scope has neither of."""

    async def cancelled(_scope: Scope, _receive: Receive, _send: Send) -> None:
        task = asyncio.current_task()
        assert task is not None
        task.cancel()
        await asyncio.sleep(0)

    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/widgets",
        "headers": [],
        "state": {SERVED: Principal(tenant_id="tenant-a", actor=PERSON)},
    }

    async def never(_message: Message) -> None:
        raise AssertionError("a cancelled request sent a message")

    async def nothing() -> Message:
        return {"type": "http.request"}

    with pytest.raises(asyncio.CancelledError):
        await RecordsRequests(cancelled)(scope, nothing, never)


class RefusingAuditEvents:
    async def record(self, _event: RequestEvent) -> None:
        raise ConnectionError("the audit events are out of reach")

    async def list(self) -> list[RecordedAuditEvent]:
        return []


class RefusingDatabase:
    """The substrate the app serves from, except that no request event can be recorded."""

    def __init__(self, database: Any) -> None:
        self._database: Any = database

    def __getattr__(self, name: str) -> Any:
        return getattr(self._database, name)

    def audit_events(self, _tenant_id: str) -> RefusingAuditEvents:
        return RefusingAuditEvents()


def test_a_request_whose_event_cannot_be_recorded_fails_and_sends_nothing_it_read(
    database: Database,
) -> None:
    app = an_app(database)
    with TestClient(app, raise_server_exceptions=False) as client:
        app.state.database = RefusingDatabase(app.state.database)
        answered = client.get("/acting", headers={TENANT_HEADER: "tenant-a"})

    assert answered.status_code == 500
    assert PERSON.subject not in answered.text
