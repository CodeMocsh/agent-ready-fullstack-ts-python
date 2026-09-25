from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import cache

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import log, telemetry
from app.identity import Unauthenticated, resolved_without_a_credential
from app.models import ErrorBody
from app.routes import public_router, router
from app.wiring import build, build_telemetry, unauthenticated_is_acknowledged


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
    log.serving(database.name, version)
    await _say_what_this_deployment_authenticates(database.name)
    app.state.database = database
    try:
        yield
    finally:
        try:
            await database.close()
        finally:
            if app.state.instruments is not None:
                app.state.instruments.shutdown()


async def _say_what_this_deployment_authenticates(substrate: str) -> None:
    """State it at every boot, and raise your voice only when nobody has said it on purpose.

    The fact is logged either way and only the level moves; `docs/adr/0008` says why a warning
    a deployment cannot acknowledge is one it learns to mute.
    """
    tenant = await resolved_without_a_credential()
    if tenant is None:
        log.identity_verified()
    elif unauthenticated_is_acknowledged():
        log.identity_open_on_purpose(tenant)
    else:
        log.identity_open(tenant, substrate)


def create_app() -> FastAPI:
    """The app, assembled. A function so a test can hold two with different substrates.

    `docs/adr/0007` holds the settings below and why each one is off. Building one configures
    the logging of the whole process: see `app.log.configure`.
    """
    log.configure()
    app = FastAPI(
        title="Tasks API",
        version="0.1.0",
        separate_input_output_schemas=False,
        lifespan=lifespan,
    )
    app.router.redirect_slashes = False
    app.include_router(public_router)
    app.include_router(router)
    app.add_exception_handler(Unauthenticated, _refuse)
    log.instrument(app)
    settings = build_telemetry()
    app.state.instruments = (
        None if settings is None else telemetry.instrument(app, settings, *telemetry.otlp(settings))
    )
    return app


async def _refuse(_request: Request, refusal: Exception) -> JSONResponse:
    """A request whose tenant could not be resolved.

    Registered though nothing this template ships raises it: the seam is meant to be replaced,
    and a replacement that had to remember its own handler would answer `500` — which a client
    retries — to every request it meant to refuse. `WWW-Authenticate` because a `401` without it
    is a `401` the caller cannot act on.
    """
    return JSONResponse(
        status_code=401,
        content=ErrorBody(detail=str(refusal)).model_dump(),
        headers={"WWW-Authenticate": "Bearer"},
    )


@cache
def served() -> FastAPI:
    """The app this module serves, built on the first call and kept."""
    return create_app()


def __getattr__(name: str) -> FastAPI:
    """`app`, built the first time something asks for it -- `uvicorn app.main:app` does.
    Importing `create_app` from here builds nothing, so `app.serve` instruments one app."""
    if name == "app":
        return served()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
