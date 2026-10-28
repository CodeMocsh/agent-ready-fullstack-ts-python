"""An app that `routed` builds answers its first request without building a route on the event
loop, as an app the lifespan started does."""

import pytest
from fastapi import routing
from fastapi.testclient import TestClient

from app.main import ROUTERS
from app.store import Database
from tests.routed import routed


def _refused(**named: object) -> None:
    raise AssertionError(f"a request built the route at {named['path']}")


def test_a_routed_app_has_built_its_routes_before_the_first_request(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = routed(*ROUTERS, database=database)
    monkeypatch.setattr(routing, "get_dependant", _refused)

    assert TestClient(app).get("/no-route-answers-this").status_code == 404
