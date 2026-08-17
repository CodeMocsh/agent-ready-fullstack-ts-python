from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.routes import router
from app.store import Database
from app.wiring import build


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """One `Database` per process, migrated before the first request and closed after the last.

    Migrating here rather than lazily is what makes a missing table a failed startup instead
    of a failed request: the process that cannot serve does not come up. `DB_MIGRATE=check`
    turns the apply into a verify, for a deployment whose schema is owned by something else.
    """
    database = build()
    await database.migrate()
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
