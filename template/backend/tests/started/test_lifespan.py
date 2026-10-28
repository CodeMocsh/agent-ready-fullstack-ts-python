"""The lifespan builds the routes before the first request."""

import pytest
from fastapi import routing
from fastapi.testclient import TestClient

from app import main


def _refused(**named: object) -> None:
    raise AssertionError(f"a request built the route at {named['path']}")


def test_the_routes_are_built_before_the_first_request(monkeypatch: pytest.MonkeyPatch) -> None:
    with TestClient(main.create_app()) as serving:
        monkeypatch.setattr(routing, "get_dependant", _refused)

        assert serving.get("/health").status_code == 200


def test_a_request_to_an_app_whose_lifespan_never_ran_builds_a_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routing, "get_dependant", _refused)

    with pytest.raises(AssertionError, match="a request built the route"):
        TestClient(main.create_app()).get("/health")
