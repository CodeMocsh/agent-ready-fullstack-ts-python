"""`app.main`: the module-level `app` is built when asked for, and not before, and a route test
assembles what the app serves."""

from fastapi import FastAPI

from app import main
from app.store import Database
from tests.routed import routed
from tests.routes.walk import routes_of


def test_importing_the_module_builds_no_app_until_app_is_read() -> None:
    main.module_app.cache_clear()

    assert main.module_app.cache_info().currsize == 0
    assert isinstance(main.app, FastAPI)
    assert main.app is main.app
    assert main.module_app.cache_info().currsize == 1


def test_any_other_name_is_still_missing() -> None:
    assert not hasattr(main, "application")


def test_a_route_test_assembles_what_the_app_serves(database: Database) -> None:
    served, assembled = main.create_app(), routed(*main.ROUTERS, database=database)

    assert (
        [repr(one) for one in served.user_middleware],
        served.exception_handlers,
        routes_of(served),
        [(type(one).__name__, getattr(one, "path", None)) for one in served.routes],
    ) == (
        [repr(one) for one in assembled.user_middleware],
        assembled.exception_handlers,
        routes_of(assembled),
        [(type(one).__name__, getattr(one, "path", None)) for one in assembled.routes],
    ), (
        "create_app serves something that routed(*ROUTERS) does not, so a route test answers "
        "differently from the app. Put each requirement's router in ROUTERS, and everything "
        "around a route in assemble."
    )
