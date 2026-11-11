import asyncio
from pathlib import Path

import pytest
from fastapi import APIRouter, Depends, Response
from fastapi.testclient import TestClient

from app import response_bound, serve
from app.audit_events import acting_as
from app.deps import resolved_principal
from app.identity.shared import SENTINEL_PRINCIPAL, SENTINEL_TENANT
from app.main import ROUTERS
from app.models import PAGE_MAX, TITLE_MAX, CreateTaskBody
from app.response_limits import DEFAULT_BYTES
from app.store import Database
from tests.pages import every_event
from tests.responses.doubles import unfinished_router
from tests.routed import assembled


async def test_a_full_page_of_large_rows_still_answers_a_complete_page(database: Database) -> None:
    store = database.store(SENTINEL_TENANT)
    with acting_as(SENTINEL_PRINCIPAL.actor):
        for _ in range(PAGE_MAX + 1):
            await store.create(CreateTaskBody(title="界" * TITLE_MAX))
    app = await asyncio.to_thread(assembled, *ROUTERS, database=database)
    client = TestClient(app)

    response = await asyncio.to_thread(client.get, "/tasks")

    assert response.status_code == 200
    assert len(response.content) <= DEFAULT_BYTES
    assert len(response.json()["tasks"]) == PAGE_MAX
    assert response.json()["next"] is not None
    assert all(task["title"] == "界" * TITLE_MAX for task in response.json()["tasks"])


def oversized_router() -> APIRouter:
    """A tenant route whose answer is over the default limit, as a route answers what its schema
    does not bound."""
    router = APIRouter(dependencies=[Depends(resolved_principal)])

    @router.get("/oversized")
    async def oversized() -> Response:
        return Response(b"x" * (DEFAULT_BYTES + 1))

    return router


async def test_a_refused_read_records_the_status_the_client_received(database: Database) -> None:
    app = await asyncio.to_thread(assembled, oversized_router(), database=database)
    client = TestClient(app)

    response = await asyncio.to_thread(client.get, "/oversized")
    events = await every_event(database.audit_events(SENTINEL_TENANT))
    requests = [event for event in events if event.method == "GET"]

    assert response.status_code == 500
    assert [event.status for event in requests] == [500]
    assert requests[0].request_id == response.headers["x-request-id"]


@pytest.mark.parametrize("one_origin", [False, True])
async def test_a_buffer_timeout_records_the_final_status(
    database: Database, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, one_origin: bool
) -> None:
    monkeypatch.setattr(response_bound, "REQUEST_BUDGET_SECONDS", 0.05)
    app = await asyncio.to_thread(assembled, unfinished_router(), database=database)
    (tmp_path / "index.html").write_text("shell")
    server = serve.create_server(tmp_path, app) if one_origin else app
    client = TestClient(server)

    response = await asyncio.to_thread(
        client.get, "/api/unfinished" if one_origin else "/unfinished"
    )
    events = await every_event(database.audit_events(SENTINEL_TENANT))

    assert response.status_code == 500
    assert [event.status for event in events if event.method == "GET"] == [500]
