"""The API over the in-memory substrate: the fast tier, and the seed data.

Seed rows are asserted here and nowhere else. `tests/store_contract.py` runs against both
substrates and so may not know about them, and `frontend/tests/api/contract.test.ts` runs against
this service and the mock handlers and may not either. This file is where "three tasks, and
one of them is `2`" is allowed to be true.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.identity.shared import SENTINEL_PRINCIPAL, SENTINEL_TENANT
from app.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A fresh app per test, which is what replaced the store's `reset()`.

    The substrate is built by the lifespan, so a new client is a new store. That is why no
    production interface here carries a test-only method."""
    with TestClient(create_app()) as fresh:
        yield fresh


def test_it_comes_up_on_the_in_memory_substrate() -> None:
    """Which substrate answered is a fact worth asserting rather than assuming. Every seed
    assertion below is true of this one and of no other, so a run that quietly reached a real
    database would fail further down and look like a bug in the app."""
    app = create_app()
    with TestClient(app):
        assert app.state.database.name == "memory"


def test_lists_seed_tasks(client: TestClient) -> None:
    response = client.get("/tasks")
    assert response.status_code == 200
    assert len(response.json()) == 3


def test_creates_a_task(client: TestClient) -> None:
    response = client.post("/tasks", json={"title": "Write a real test"})
    assert response.status_code == 201
    assert response.json()["done"] is False


def test_updates_a_task(client: TestClient) -> None:
    response = client.patch("/tasks/2", json={"done": True})
    assert response.status_code == 200
    assert response.json()["done"] is True


def test_missing_task_is_404(client: TestClient) -> None:
    assert client.patch("/tasks/999", json={"done": True}).status_code == 404
    assert client.delete("/tasks/999").status_code == 404


def test_deletes_a_task(client: TestClient) -> None:
    assert client.delete("/tasks/1").status_code == 204
    assert len(client.get("/tasks").json()) == 2


def test_a_trailing_slash_is_a_404_and_never_a_redirect(client: TestClient) -> None:
    """`redirect_slashes = False` in `create_app`, and nothing else would catch it going.

    The spec is byte-identical either way, so `make openapi-check` cannot see this one.
    `docs/adr/template/0008` holds why the redirect is refused.
    """
    response = client.get("/tasks/", follow_redirects=False)

    assert response.status_code == 404
    assert "location" not in response.headers


def test_two_apps_do_not_share_a_store() -> None:
    """The other half of dropping `reset()`. Two apps in one process, because that is the
    property the fixture relies on and the only way to assert it without depending on the
    order pytest happens to run these in."""
    with TestClient(create_app()) as first, TestClient(create_app()) as second:
        assert first.delete("/tasks/1").status_code == 204

        assert len(first.get("/tasks").json()) == 2
        assert len(second.get("/tasks").json()) == 3


def test_a_request_records_who_it_served_and_which_request_it_was() -> None:
    """The event names the actor the seam resolved, and the request id the response carries, so
    an auditor can join the event to the request's log line."""
    app = create_app()
    with TestClient(app) as client:
        answered = client.get("/tasks")
        assert client.portal is not None
        events = client.portal.call(app.state.database.audit_events(SENTINEL_TENANT).list)

    [event] = events
    assert event.actor == SENTINEL_PRINCIPAL.actor
    assert event.action == "read"
    assert event.resource_ids == tuple(task["id"] for task in answered.json())
    assert event.request_id == answered.headers["x-request-id"]
