import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.routes import router
from app.store import Database
from app.wiring import build

_LOG = logging.getLogger("uvicorn.error")
"""The logger the server has already configured.

`getLogger(__name__)` is the obvious choice and it is the wrong one: uvicorn configures its
own loggers and leaves the root logger untouched, so an `INFO` record from `app.main`
propagates to a root that has no handler and is silently dropped. Verified by running both.
Falling back is harmless under another server -- an unconfigured logger is exactly as quiet as
the one this replaces.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """One `Database` per process, verified before the first request and closed after the last.

    Verified, never applied: DDL is a release step (`make migrate`) and this process holds no
    rights to it. Checking here rather than lazily is what makes a skipped release step a
    failed startup instead of a failed request — the process that cannot serve does not come
    up, and the deploy fails where somebody is watching.
    """
    database = build()
    version = await database.check()
    _LOG.info("serving on the %s substrate, schema %s", database.name, version)
    app.state.database = database
    try:
        yield
    finally:
        await database.close()


def create_app() -> FastAPI:
    """The app, assembled. A function so a test can hold two with different substrates."""
    app = FastAPI(
        title="Tasks API",
        version="0.1.0",
        separate_input_output_schemas=False,
        lifespan=lifespan,
    )
    app.router.redirect_slashes = False
    app.include_router(router)
    return app


app = create_app()


def database_of(app: FastAPI) -> Database:
    """The substrate this app is serving from. Raises before the lifespan has run."""
    database = getattr(app.state, "database", None)
    if database is None:
        raise RuntimeError("no database on app.state: the lifespan has not run")
    return database
