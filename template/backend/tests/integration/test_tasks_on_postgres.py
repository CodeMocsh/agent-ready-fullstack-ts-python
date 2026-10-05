"""The example resource through the whole stack on Postgres: HTTP, the wiring, the pool and
real SQL. `example_resource` covers this file with the example."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.integration.conftest import Provisioned


def test_the_whole_stack_serves_a_task_round_trip(
    provisioned: Provisioned, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HTTP through the wiring, the pool and real SQL, as the role a deployment runs as.

    Sync rather than async, and deliberately: `TestClient` drives the app's lifespan on its
    own event loop, so this is the one suite that exercises `create_app()` the way uvicorn
    does -- including the schema check that runs before the first request, against a schema
    something else applied.
    """
    monkeypatch.setenv("DATABASE_URL", provisioned.app_dsn)
    monkeypatch.setenv("DB_SCHEMA", provisioned.schema)

    with TestClient(create_app()) as client:
        assert client.get("/tasks").json() == []

        created = client.post("/tasks", json={"title": "Round trip"})
        assert created.status_code == 201
        task = created.json()
        assert task["done"] is False

        assert [row["id"] for row in client.get("/tasks").json()] == [task["id"]]

        patched = client.patch(f"/tasks/{task['id']}", json={"done": True})
        assert patched.status_code == 200
        assert patched.json()["done"] is True

        assert client.patch("/tasks/not-a-uuid", json={"done": True}).status_code == 404
        assert client.delete("/tasks/not-a-uuid").status_code == 404

        assert client.delete(f"/tasks/{task['id']}").status_code == 204
        assert client.get("/tasks").json() == []
