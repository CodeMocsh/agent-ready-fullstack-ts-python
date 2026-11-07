"""The example resource through the whole stack on Postgres: HTTP, the wiring, the pool and
real SQL, and a job the worker process ends. `example_resource` covers this file with the
example."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.clock import hold_the_thread
from tests.integration.conftest import Provisioned

BACKEND: Final = Path(__file__).resolve().parents[2]

WITHIN: Final = 15.0
"""The longest the test waits for the worker to end a job, and then to stop."""


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
        assert client.get("/tasks").json() == {"tasks": [], "next": None}

        created = client.post("/tasks", json={"title": "Round trip"})
        assert created.status_code == 201
        task = created.json()
        assert task["done"] is False

        assert [row["id"] for row in client.get("/tasks").json()["tasks"]] == [task["id"]]

        patched = client.patch(f"/tasks/{task['id']}", json={"done": True})
        assert patched.status_code == 200
        assert patched.json()["done"] is True

        assert client.patch("/tasks/not-a-uuid", json={"done": True}).status_code == 404
        assert client.delete("/tasks/not-a-uuid").status_code == 404

        assert client.delete(f"/tasks/{task['id']}").status_code == 204
        assert client.get("/tasks").json() == {"tasks": [], "next": None}


def test_a_task_closes_through_a_job_that_the_worker_process_ends(
    provisioned: Provisioned, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two processes, as a deployment runs them: this one serves the request that asks for the
    job, and `python -m app.worker` ends it. Each reads the other's work only through Postgres.
    `docs/adr/template/0027`."""
    monkeypatch.setenv("DATABASE_URL", provisioned.app_dsn)
    monkeypatch.setenv("DB_SCHEMA", provisioned.schema)
    worker = subprocess.Popen(
        [sys.executable, "-m", "app.worker"],
        cwd=BACKEND,
        env={**os.environ, "APP_ENV": "development"},
        stdout=subprocess.DEVNULL,
    )
    try:
        with TestClient(create_app()) as client:
            task = client.post("/tasks", json={"title": "Close me"}).json()
            asked = client.post(f"/tasks/{task['id']}/close")
            assert asked.status_code == 202
            job = _ended(client, asked.json()["id"])
            assert (job["state"], job["outcome"]) == ("done", {"closed": True})
            assert client.get("/tasks").json()["tasks"][0]["done"] is True
    finally:
        worker.send_signal(signal.SIGTERM)
        stopped = worker.wait(timeout=WITHIN)
    assert stopped == 0


def _ended(client: TestClient, id: str) -> dict[str, Any]:
    deadline = time.monotonic() + WITHIN
    while time.monotonic() < deadline:
        job: dict[str, Any] = client.get(f"/jobs/{id}").json()
        if job["state"] != "pending":
            return job
        hold_the_thread(0.05)
    raise AssertionError(f"the job {id} was still pending after {WITHIN} s")
