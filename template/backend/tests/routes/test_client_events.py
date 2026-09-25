"""`POST /client-events`: what the browser may report, and what it may not.

The route is public and its input is untrusted by construction -- anybody can post to it. So
the model is the whole defence: every field is an enum, a bounded identifier or a number, and a
refusal is a `422` rather than a line in the log.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import MAX_CLIENT_EVENTS
from tests.conftest import Logged

CANARY = "canary-6f1e2d-alice@example.com"


def an_event(**changed: Any) -> dict[str, Any]:
    return {
        "kind": "query",
        "route": "/tasks/$id",
        "error": "ApiError",
        "status": 500,
        "request_id": "0f8c2c1b9d2e4b6f8a1c3e5d7f9b0a2c",
    } | changed


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, logged: Logged) -> Iterator[TestClient]:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with TestClient(create_app()) as fresh:
        logged()
        yield fresh


def reported(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [one for one in lines if one["message"] == "client event"]


def test_an_event_is_accepted_and_logged_as_the_client_sent_it(
    client: TestClient, logged: Logged
) -> None:
    answered = client.post("/client-events", json={"events": [an_event()]})

    assert answered.status_code == 204
    [line] = reported(logged())
    assert line["severity"] == "WARNING"
    assert line["source"] == "client"
    assert line["kind"] == "query"
    assert line["route"] == "/tasks/$id"
    assert line["error"] == "ApiError"
    assert line["status"] == 500
    assert line["failed_request_id"] == "0f8c2c1b9d2e4b6f8a1c3e5d7f9b0a2c"


def test_an_event_with_nothing_to_name_is_accepted(client: TestClient, logged: Logged) -> None:
    """An error thrown before the router matched, with no request behind it."""
    answered = client.post(
        "/client-events",
        json={"events": [an_event(kind="uncaught", route=None, status=None, request_id=None)]},
    )

    assert answered.status_code == 204
    assert len(reported(logged())) == 1


@pytest.mark.parametrize(
    "refused",
    [
        pytest.param(an_event(error=CANARY), id="free text as the error name"),
        pytest.param(an_event(route=f"/tasks/{CANARY}"), id="free text in the route"),
        pytest.param(an_event(request_id=CANARY), id="free text as the request id"),
        pytest.param(an_event(message=CANARY), id="a field nobody declared"),
        pytest.param(an_event(kind="debug"), id="a kind nobody declared"),
        pytest.param(an_event(status=42), id="a status that is not one"),
    ],
)
def test_anything_that_could_carry_free_text_is_refused_and_not_logged(
    client: TestClient, logged: Logged, refused: dict[str, Any]
) -> None:
    answered = client.post("/client-events", json={"events": [refused]})

    assert answered.status_code == 422
    assert reported(logged()) == []


def test_a_batch_is_bounded_both_ways(client: TestClient, logged: Logged) -> None:
    assert client.post("/client-events", json={"events": []}).status_code == 422
    too_many = [an_event()] * (MAX_CLIENT_EVENTS + 1)
    assert client.post("/client-events", json={"events": too_many}).status_code == 422
    assert reported(logged()) == []
