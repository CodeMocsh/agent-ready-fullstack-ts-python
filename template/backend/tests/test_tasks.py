import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import task_store


@pytest.fixture(autouse=True)
def _reset_store() -> None:
    task_store.reset()


client = TestClient(app)


def test_lists_seed_tasks() -> None:
    response = client.get("/tasks")
    assert response.status_code == 200
    assert len(response.json()) == 3


def test_creates_a_task() -> None:
    response = client.post("/tasks", json={"title": "Write a real test"})
    assert response.status_code == 201
    assert response.json()["done"] is False


def test_updates_a_task() -> None:
    response = client.patch("/tasks/2", json={"done": True})
    assert response.status_code == 200
    assert response.json()["done"] is True


def test_missing_task_is_404() -> None:
    assert client.patch("/tasks/999", json={"done": True}).status_code == 404
    assert client.delete("/tasks/999").status_code == 404


def test_deletes_a_task() -> None:
    assert client.delete("/tasks/1").status_code == 204
    assert len(client.get("/tasks").json()) == 2
