"""The API over the in-memory substrate: the fast tier, and the seed data.

Seed rows are asserted here and nowhere else. `tests/store_contract.py` runs against both
substrates and so may not know about them, and `frontend/tests/api/contract.test.ts` runs against
this service and the mock handlers and may not either. This file is where "three tasks, and
one of them is `2`" is allowed to be true.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.audit_events import acting_as
from app.identity.shared import SENTINEL_PRINCIPAL, SENTINEL_TENANT
from app.models import Job
from app.routes import tenant
from app.store import Database
from app.store.memory import MemoryDatabase
from app.task_jobs import CLOSE_A_TASK
from tests.pages import every_event
from tests.routed import routed


@pytest.fixture
def client(database: Database) -> Iterator[TestClient]:
    """A fresh substrate per test, which is what replaced the store's `reset()`. That is why no
    production interface here carries a test-only method."""
    with TestClient(routed(tenant.router, database=database)) as fresh:
        yield fresh


def test_lists_seed_tasks(client: TestClient) -> None:
    response = client.get("/tasks")
    assert response.status_code == 200
    assert len(response.json()["tasks"]) == 3
    assert response.json()["next"] is None


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
    assert len(client.get("/tasks").json()["tasks"]) == 2


def test_lists_the_newest_task_first(client: TestClient) -> None:
    created = client.post("/tasks", json={"title": "The newest"}).json()

    assert client.get("/tasks").json()["tasks"][0] == created


def test_the_next_page_starts_after_the_task_it_names(client: TestClient) -> None:
    response = client.get("/tasks", params={"after": "2"})

    assert response.status_code == 200
    assert [task["id"] for task in response.json()["tasks"]] == ["1"]


def test_a_page_after_a_task_that_is_not_there_is_404(client: TestClient) -> None:
    response = client.get("/tasks", params={"after": "999"})

    assert response.status_code == 404
    assert response.json() == {"detail": "Task not found"}


def test_a_trailing_slash_is_a_404_and_never_a_redirect(client: TestClient) -> None:
    """`redirect_slashes = False` in `assemble`, and nothing else would catch it going.

    The spec is byte-identical either way, so `make openapi-check` cannot see this one.
    `docs/adr/template/0008` holds why the redirect is refused.
    """
    response = client.get("/tasks/", follow_redirects=False)

    assert response.status_code == 404
    assert "location" not in response.headers


def test_a_request_records_who_it_served_and_which_request_it_was(
    client: TestClient, database: Database
) -> None:
    """The event names the actor the seam resolved, and the request id the response carries, so
    an auditor can join the event to the request's log line."""
    answered = client.patch("/tasks/does-not-exist", json={"done": True})
    assert client.portal is not None
    events = client.portal.call(every_event, database.audit_events(SENTINEL_TENANT))

    [event] = events
    assert event.actor == SENTINEL_PRINCIPAL.actor
    assert (event.method, event.route, event.resource_ids, event.status) == (
        "PATCH",
        "/tasks/{id}",
        ("does-not-exist",),
        404,
    )
    assert event.request_id == answered.headers["x-request-id"]


def test_closing_a_task_asks_for_a_job_that_marks_it_done(
    client: TestClient, database: Database
) -> None:
    response = client.post("/tasks/2/close")

    assert response.status_code == 202
    assert response.json()["state"] == "pending"
    assert isinstance(database, MemoryDatabase)
    assert client.portal is not None
    client.portal.call(database.drained)
    assert client.get(f"/jobs/{response.json()['id']}").json()["outcome"] == {"closed": True}
    assert [task["done"] for task in client.get("/tasks").json()["tasks"] if task["id"] == "2"] == [
        True
    ]


def test_closing_a_task_that_is_not_there_ends_the_job_with_nothing_closed(
    client: TestClient, database: Database
) -> None:
    asked = client.post("/tasks/999/close").json()

    assert isinstance(database, MemoryDatabase)
    assert client.portal is not None
    client.portal.call(database.drained)
    assert client.get(f"/jobs/{asked['id']}").json()["outcome"] == {"closed": False}


def test_a_job_that_is_not_there_is_404(client: TestClient) -> None:
    response = client.get("/jobs/999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Job not found"}


def test_the_job_of_another_tenant_is_404(client: TestClient, database: Database) -> None:
    async def asked_elsewhere() -> Job:
        with acting_as(SENTINEL_PRINCIPAL.actor):
            return await database.jobs("a-second-tenant").ask(CLOSE_A_TASK, {"id": "1"})

    assert client.portal is not None
    asked = client.portal.call(asked_elsewhere)

    assert client.get(f"/jobs/{asked.id}").status_code == 404


def test_closing_a_task_whose_id_cannot_be_stored_is_refused(client: TestClient) -> None:
    response = client.post("/tasks/a%00b/close")

    assert response.status_code == 422
    assert response.json() == {"detail": "The input holds a value that cannot be stored"}
