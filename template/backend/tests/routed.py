"""The app a route test drives: the routers under test, assembled as `create_app` assembles its
own, with no lifespan.

A route test asserts the HTTP contract: the requirement, the refusals and their detail, the
statuses and the shapes. None of that needs the process `app/lifespan.py` starts, and a project's
lifespan grows with the project. Outside `tests/started/` and the tiers, `tests/conftest.py`
refuses a test that enters it. `docs/adr/template/0018`.

**`routed` builds one shared app for each set of routers in the process.** It shares that app with
one test at a time, with a new `app.state` and no `dependency_overrides`. The test may not change
what the app serves. `take_back` ends the share after the teardown of the test, and raises
`SharedAppChanged` when the test changed it. A test that changes its app builds it with
`assembled`.

The template owns this module. A project hands `routed` each value its lifespan puts on
`app.state`, by name, and does not edit it. The `app_state` fixture holds those values.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, override

from fastapi import APIRouter, FastAPI
from starlette.datastructures import State
from starlette.middleware import Middleware
from starlette.routing import BaseRoute

from app import log
from app.lifespan import build_routes
from app.main import assemble
from app.store import Database
from app.wiring import build_service_version, build_telemetry


class SharedAppChanged(RuntimeError):
    """A test changed what a shared app serves."""


class SharedTwiceInOneTest(RuntimeError):
    """A test asked `routed` twice for the same shared app."""


class SharedPastItsTest(RuntimeError):
    """Something read the state of a shared app after the test it was shared with ended."""


class SharedWithTelemetry(RuntimeError):
    """A test asked `routed` for an app while the environment configures telemetry."""


_CHANGED_BY_EACH_TEST: frozenset[str] = frozenset(
    {
        "state",
        "dependency_overrides",
        "middleware_stack",
        "openapi_schema",
        "_openapi_routes_version",
    }
)


@dataclass(frozen=True)
class _Served:
    app: dict[str, object]
    router: dict[str, object]
    routes: tuple[BaseRoute, ...]
    middleware: tuple[Middleware, ...]
    handlers: tuple[tuple[Any, Any], ...]

    def is_served_by(self, app: FastAPI) -> bool:
        now = _served(app)
        return (
            _same(self.app, now.app)
            and _same(self.router, now.router)
            and _same_items(self.routes, now.routes)
            and _same_items(self.middleware, now.middleware)
            and self.handlers == now.handlers
        )


def _same(then: dict[str, object], now: dict[str, object]) -> bool:
    return then.keys() == now.keys() and all(then[name] is now[name] for name in then)


def _same_items(then: tuple[object, ...], now: tuple[object, ...]) -> bool:
    return len(then) == len(now) and all(one is other for one, other in zip(then, now, strict=True))


def _served(app: FastAPI) -> _Served:
    return _Served(
        {name: value for name, value in vars(app).items() if name not in _CHANGED_BY_EACH_TEST},
        dict(vars(app.router)),
        tuple(context.original_route for context in build_routes(app)),
        tuple(app.user_middleware),
        tuple(app.exception_handlers.items()),
    )


@dataclass(frozen=True)
class _Shared:
    app: FastAPI
    served: _Served
    assembled_state: dict[str, Any]


_SHARED: dict[tuple[int, ...], _Shared] = {}
_SHARED_WITH_THIS_TEST: list[tuple[int, ...]] = []


class _Ended(State):
    def __init__(self, test: str) -> None:
        super().__init__({"test": test})

    def _past(self, key: object) -> SharedPastItsTest:
        return SharedPastItsTest(
            f"Something read app.state.{key} of the app that tests.routed.routed shared with "
            f"{self._state['test']}, after that test ended. A share lasts for one test. Ask "
            f"for it in a fixture that each test gets for itself, or build one with "
            f"tests.routed.assembled."
        )

    @override
    def __getattr__(self, key: Any) -> Any:
        raise self._past(key)

    @override
    def __getitem__(self, key: str) -> Any:
        raise self._past(key)

    @override
    def __setattr__(self, key: Any, value: Any) -> None:
        raise self._past(key)

    @override
    def __setitem__(self, key: str, value: Any) -> None:
        raise self._past(key)

    @override
    def __iter__(self) -> Iterator[str]:
        raise self._past("keys")

    @override
    def __len__(self) -> int:
        raise self._past("keys")


def routed(*routers: APIRouter, database: Database, **state: object) -> FastAPI:
    """The shared app that serves `routers`, with everything `assemble` puts around a route, and
    each of its routes built, as the lifespan builds them. Its `app.state` holds what `assemble`
    put there, `database`, and each of `state` under its name. It has no `dependency_overrides`
    and no OpenAPI schema yet, and its log lines name the `service.version` the environment names.

    Shares by the identity of `routers`: a router built in each test builds a new app each time,
    and the process keeps it. Raises `SharedTwiceInOneTest` when this test asked for it already,
    and `SharedWithTelemetry` when the environment configures telemetry. Starts nothing. Blocks
    the first time it builds the app for `routers`: call it off the event loop."""
    if build_telemetry() is not None:
        raise SharedWithTelemetry(
            "The environment configures telemetry, and an instrumented app cannot be shared. "
            "Build this app with tests.routed.assembled."
        )
    key = tuple(id(router) for router in routers)
    if key in _SHARED_WITH_THIS_TEST:
        raise SharedTwiceInOneTest(
            "This test asked tests.routed.routed twice for the app that serves the same routers, "
            "and there is one such app. Build each app with tests.routed.assembled."
        )
    if key not in _SHARED:
        app = assemble(*routers, lifespan=None)
        assembled_state = {name: app.state[name] for name in app.state}
        _SHARED[key] = _Shared(app, _served(app), assembled_state)
    shared = _SHARED[key]
    log.configure(build_service_version())
    shared.app.dependency_overrides.clear()
    shared.app.openapi_schema = None
    shared.app.state = State({**shared.assembled_state, "database": database, **state})
    _SHARED_WITH_THIS_TEST.append(key)
    return shared.app


def assembled(*routers: APIRouter, database: Database, **state: object) -> FastAPI:
    """A new app, as `routed` shares it, that only the test that asks for it has. The test may
    change it. Builds every route on each call. Blocks: call it off the event loop."""
    app = assemble(*routers, lifespan=None)
    app.state.database = database
    for name, value in state.items():
        setattr(app.state, name, value)
    _ = build_routes(app)
    return app


def take_back(test: str) -> None:
    """Ends each share `routed` made since the last call, with `test`. Raises `SharedAppChanged`
    when `test` changed what a shared app serves: its attributes, its routes, the routes of each
    router it includes, its middleware, or its exception handlers. `routed` then builds that app
    again."""
    shared = [*_SHARED_WITH_THIS_TEST]
    _SHARED_WITH_THIS_TEST.clear()
    changed = [key for key in shared if not _SHARED[key].served.is_served_by(_SHARED[key].app)]
    for key in shared:
        _SHARED[key].app.state = _Ended(test)
    for key in changed:
        del _SHARED[key]
    if changed:
        raise SharedAppChanged(
            f"{test} changed what an app that tests.routed.routed shares serves: an attribute, a "
            f"route, the routes of a router it includes, a middleware or an exception handler. "
            f"Build an app that a test changes with tests.routed.assembled."
        )
