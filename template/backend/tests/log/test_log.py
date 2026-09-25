"""The log: one JSON object per line, named so every cloud reads it, and nothing in it that a
request carried.

Driven through a real `TestClient`, because the guarantee is about what reaches stdout -- a
test that called the event functions directly would keep passing after somebody stopped
calling them, or started logging somewhere else.
"""

import json
import logging
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import Logged

CANARY = "canary-6f1e2d-alice@example.com"
"""A value no route has a reason to log. Sent in every place a request can carry one, and
refused everywhere in the output."""

SEVERITIES = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    return create_app()


@pytest.fixture
def client(app: FastAPI, logged: Logged) -> Iterator[TestClient]:
    """A served app whose boot lines are already read, so a test sees only its own requests."""
    with TestClient(app) as fresh:
        logged()
        yield fresh


def completed(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [one for one in lines if one["message"] == "request completed"]


def test_every_line_is_one_json_object_with_the_fields_every_cloud_reads(
    client: TestClient, logged: Logged
) -> None:
    client.get("/tasks")

    lines = logged()

    assert lines
    for line in lines:
        assert line["severity"] in SEVERITIES
        assert line["time"].endswith("Z")
        assert isinstance(line["message"], str)
        assert isinstance(line["logger"], str)


def test_the_boot_lines_are_in_the_same_format(app: FastAPI, logged: Logged) -> None:
    """The lines a deployment greps first -- which substrate, whether it authenticates -- are
    written before any request, so they are the ones a request-only test would miss."""
    with TestClient(app):
        pass

    said = {line["message"]: line for line in logged()}

    assert said["serving"]["substrate"] == "memory"
    assert said["serving"]["severity"] == "INFO"


def test_a_request_is_logged_once_by_its_route_template_and_the_id_it_answered_with(
    client: TestClient, logged: Logged
) -> None:
    answered = client.patch("/tasks/does-not-exist", json={"done": True})

    [line] = completed(logged())

    assert line["http.request.method"] == "PATCH"
    assert line["http.route"] == "/tasks/{id}"
    assert line["http.response.status_code"] == 404
    assert line["request_id"] == answered.headers["x-request-id"]
    assert line["duration_ms"] >= 0


def test_a_request_no_route_answers_is_logged_without_a_route(
    client: TestClient, logged: Logged
) -> None:
    client.get("/no-route-answers-this")

    [line] = completed(logged())

    assert line["http.route"] is None
    assert line["http.response.status_code"] == 404


def test_a_request_id_the_client_sent_is_kept(client: TestClient, logged: Logged) -> None:
    """So an event the browser reports can name the request that failed, and the two lines
    join up."""
    sent = uuid4().hex

    answered = client.get("/tasks", headers={"x-request-id": sent})

    assert answered.headers["x-request-id"] == sent
    assert completed(logged())[0]["request_id"] == sent


def test_a_request_id_that_is_not_one_is_replaced(client: TestClient, logged: Logged) -> None:
    answered = client.get("/tasks", headers={"x-request-id": CANARY})

    assert answered.headers["x-request-id"] != CANARY
    assert CANARY not in json.dumps(logged())


def test_nothing_the_request_carried_reaches_the_log(client: TestClient, logged: Logged) -> None:
    """The body, the query string, a header, a path parameter and a refused body -- each the
    way a real leak has happened. The count is asserted as well, because an empty log passes
    the absence check just as happily as a clean one."""
    client.post(f"/tasks?note={CANARY}", json={"title": CANARY}, headers={"x-note": CANARY})
    client.patch(f"/tasks/{CANARY}", json={"done": True})
    client.post("/tasks", json={"title": {"nested": CANARY}})
    client.get(f"/no-route/{CANARY}")

    lines = logged()

    assert len(completed(lines)) == 4
    assert CANARY not in json.dumps(lines)


def test_an_exception_a_request_raised_is_logged_as_a_500(app: FastAPI, logged: Logged) -> None:
    @app.get("/raises")
    async def raises() -> None:
        raise RuntimeError("the handler failed")

    with TestClient(app, raise_server_exceptions=False) as client:
        logged()
        client.get("/raises")

    [line] = completed(logged())

    assert line["http.response.status_code"] == 500
    assert line["http.route"] == "/raises"


@pytest.mark.usefixtures("client")
def test_a_library_record_carries_its_traceback_in_one_field(logged: Logged) -> None:
    """What uvicorn writes when a request raises: a foreign record, from a logger this project
    does not own. A multi-line traceback breaks every log agent that reads stdout, and
    `exception` is the field GCP's Error Reporting groups on."""
    try:
        raise RuntimeError("the handler failed")
    except RuntimeError:
        logging.getLogger("uvicorn.error").exception("Exception in ASGI application")

    [line] = logged()

    assert line["severity"] == "ERROR"
    assert line["logger"] == "uvicorn.error"
    assert line["exception"].startswith("Traceback")
    assert "RuntimeError: the handler failed" in line["exception"]


@pytest.mark.usefixtures("client")
def test_uvicorn_access_records_never_reach_the_log(logged: Logged) -> None:
    """The record uvicorn builds carries the path with its query string. `request completed`
    replaces it."""
    logging.getLogger("uvicorn.access").info(
        '%s - "GET /tasks?note=%s HTTP/1.1" 200', "::1", CANARY
    )

    assert logged() == []


@pytest.mark.usefixtures("client")
def test_a_library_says_nothing_below_a_warning(logged: Logged) -> None:
    """An HTTP client logs every URL it requests at `INFO`, and a URL carries whatever its query
    string does. Below `WARNING` the log is this project's own lines and uvicorn's lifecycle."""
    logging.getLogger("httpx").info("HTTP Request: GET https://example.com/?email=%s", CANARY)
    logging.getLogger("httpx").warning("a library warning")

    assert [line["message"] for line in logged()] == ["a library warning"]
