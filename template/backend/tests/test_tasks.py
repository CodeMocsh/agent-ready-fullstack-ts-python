"""The API over the in-memory substrate: the fast tier, and the seed data.

Seed rows are asserted here and nowhere else. `tests/test_store_contract.py` runs against both
substrates and so may not know about them, and `frontend/tests/contract.test.ts` runs against
this service and the mock handlers and may not either. This file is where "three tasks, and
one of them is `2`" is allowed to be true.

`DATABASE_URL` is unset per test rather than assumed absent. Without that, a developer with
one exported would run this suite against Postgres, where none of the seed rows exist -- and
the failure would look like a bug in the app.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A fresh app per test, which is what replaced the store's `reset()`.

    The substrate is built by the lifespan, so a new client is a new store. That is why no
    production interface here carries a test-only method."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with TestClient(create_app()) as fresh:
        yield fresh


def test_it_comes_up_on_the_in_memory_substrate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Which substrate answered is a fact worth asserting rather than assuming. Every seed
    assertion below is true of this one and of no other, so a run that quietly reached a real
    database would fail further down and look like a bug in the app."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
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


def test_two_apps_do_not_share_a_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other half of dropping `reset()`. Two apps in one process, because that is the
    property the fixture relies on and the only way to assert it without depending on the
    order pytest happens to run these in."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with TestClient(create_app()) as first, TestClient(create_app()) as second:
        assert first.delete("/tasks/1").status_code == 204

        assert len(first.get("/tasks").json()) == 2
        assert len(second.get("/tasks").json()) == 3
