"""The app a route test drives: the routers under test, assembled as `create_app` assembles its
own, with no lifespan.

A route test asserts the HTTP contract: the requirement, the refusals and their detail, the
statuses and the shapes. None of that needs the process `app/lifespan.py` starts, and a project's
lifespan grows with the project. Outside `tests/started/` and the tiers, `tests/conftest.py`
refuses a test that enters it. `docs/adr/template/0018`.

The template owns this module. A project hands `routed` each value its lifespan puts on
`app.state`, by name, and does not edit it. The `app_state` fixture holds those values.
"""

from fastapi import APIRouter, FastAPI

from app.lifespan import build_routes
from app.main import assemble
from app.store import Database


def routed(*routers: APIRouter, database: Database, **state: object) -> FastAPI:
    """An app that serves `routers` with everything `assemble` puts around a route, with
    `database` and each of `state` on `app.state` under its name, and each of its routes built, as
    the lifespan builds them. Starts nothing, so a test may build one for itself. Blocks: call it
    off the event loop."""
    app = assemble(*routers, lifespan=None)
    app.state.database = database
    for name, value in state.items():
        setattr(app.state, name, value)
    _ = build_routes(app)
    return app
