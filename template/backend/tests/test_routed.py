"""An app that `routed` builds answers its first request without building a route on the event
loop, as an app the lifespan started does. `routed` shares it with one test at a time, and holds
each test to what the app serves."""

from collections.abc import Callable
from typing import Final

import pytest
from fastapi import APIRouter, FastAPI, Request, Response, routing
from fastapi.testclient import TestClient
from starlette.datastructures import State
from starlette.middleware.gzip import GZipMiddleware

from app.deployment import OTLP_ENDPOINT_ENV, SERVICE_NAME_ENV
from app.identity.shared import SENTINEL_TENANT
from app.main import ROUTERS
from app.store import Database
from app.store.memory import MemoryDatabase
from tests.routed import (
    SharedAppChanged,
    SharedPastItsTest,
    SharedTwiceInOneTest,
    SharedWithTelemetry,
    assembled,
    routed,
    take_back,
)

SHARED: Final = APIRouter()
EARLIER: Final = "tests/test_routed.py::an_earlier_test"


@SHARED.get("/shared")
async def shared() -> str:
    return "shared"


def _refused(**named: object) -> None:
    raise AssertionError(f"a request built the route at {named['path']}")


def test_a_routed_app_has_built_its_routes_before_the_first_request(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = routed(*ROUTERS, database=database)
    monkeypatch.setattr(routing, "get_dependant", _refused)

    assert TestClient(app).get("/no-route-answers-this").status_code == 404


def test_an_assembled_app_has_built_its_routes_before_the_first_request(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = assembled(*ROUTERS, database=database)
    monkeypatch.setattr(routing, "get_dependant", _refused)

    assert TestClient(app).get("/no-route-answers-this").status_code == 404


def test_the_next_test_gets_the_same_app_with_only_its_own_state(database: Database) -> None:
    earlier = routed(SHARED, database=MemoryDatabase(seed_tenant=SENTINEL_TENANT), left="behind")
    earlier.dependency_overrides[shared] = lambda: "overridden"
    take_back(EARLIER)

    app = routed(SHARED, database=database)

    assert app is earlier
    assert app.state.database is database
    assert "left" not in app.state
    assert app.state.instruments is None
    assert app.dependency_overrides == {}


def test_an_assembled_app_is_never_shared(database: Database) -> None:
    assert assembled(SHARED, database=database) is not assembled(SHARED, database=database)


def test_a_test_that_asks_twice_for_the_same_app_fails(database: Database) -> None:
    _ = routed(SHARED, database=database)

    with pytest.raises(SharedTwiceInOneTest, match="tests.routed.assembled"):
        routed(SHARED, database=database)


async def _handled(_request: Request, _error: Exception) -> Response:
    return Response(status_code=418)


def _a_router() -> APIRouter:
    router = APIRouter()
    router.add_api_route("/shared", shared)
    return router


def _with_a_route(app: FastAPI, _router: APIRouter) -> None:
    app.add_api_route("/added", shared)


def _with_a_route_in_an_included_router(_app: FastAPI, router: APIRouter) -> None:
    router.add_api_route("/added", shared)


def _with_a_middleware(app: FastAPI, _router: APIRouter) -> None:
    app.add_middleware(GZipMiddleware)


def _with_an_exception_handler(app: FastAPI, _router: APIRouter) -> None:
    app.add_exception_handler(LookupError, _handled)


def _instrumented_as_an_instrumentor_does(app: FastAPI, _router: APIRouter) -> None:
    app.build_middleware_stack = app.build_middleware_stack


def _with_another_router_setting(app: FastAPI, _router: APIRouter) -> None:
    app.router.redirect_slashes = True


@pytest.mark.parametrize(
    "change",
    [
        _with_a_route,
        _with_a_route_in_an_included_router,
        _with_a_middleware,
        _with_an_exception_handler,
        _instrumented_as_an_instrumentor_does,
        _with_another_router_setting,
    ],
)
def test_a_test_that_changes_what_a_shared_app_serves_fails_and_the_next_gets_a_new_app(
    change: Callable[[FastAPI, APIRouter], None], database: Database
) -> None:
    router = _a_router()
    changed = routed(router, database=MemoryDatabase(seed_tenant=SENTINEL_TENANT))
    change(changed, router)

    with pytest.raises(SharedAppChanged, match=f"{EARLIER} changed .* tests.routed.assembled"):
        take_back(EARLIER)

    assert routed(router, database=database) is not changed


def test_the_state_and_overrides_a_test_changes_do_not_count_as_a_change(
    database: Database,
) -> None:
    app = routed(SHARED, database=database)
    app.state.added = "by the test"
    app.dependency_overrides = {shared: lambda: "overridden"}
    _ = TestClient(app).get("/openapi.json")

    take_back(EARLIER)


def test_an_environment_that_configures_telemetry_gets_no_shared_app(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(OTLP_ENDPOINT_ENV, "http://collector:4318")
    monkeypatch.setenv(SERVICE_NAME_ENV, "tasks")

    with pytest.raises(SharedWithTelemetry, match="tests.routed.assembled"):
        routed(SHARED, database=database)


def _read(state: State) -> object:
    return state.database


def _read_by_key(state: State) -> object:
    return state["database"]


def _look_up(state: State) -> object:
    return "database" in state


def _count(state: State) -> object:
    return len(state)


def _write(state: State) -> object:
    state.database = None
    return None


@pytest.mark.parametrize("use", [_read, _read_by_key, _look_up, _count, _write])
def test_state_used_after_its_test_ended_fails_and_names_that_test(
    use: Callable[[State], object], database: Database
) -> None:
    held = routed(SHARED, database=database)
    take_back(EARLIER)

    with pytest.raises(SharedPastItsTest, match=f"of the app .* with {EARLIER}"):
        use(held.state)
