from functools import cache

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import log, telemetry
from app.identity import Unauthenticated
from app.lifespan import lifespan
from app.models import ErrorBody
from app.routes import public, tenant
from app.wiring import build_gcp_project, build_service_version, build_telemetry


def create_app() -> FastAPI:
    """The app, assembled. A function so a test can hold two with different substrates.

    `docs/adr/template/0007` holds the settings below and why each one is off. Building one configures
    the logging of the whole process: see `app.log.configure`.
    """
    log.configure(build_service_version(), build_gcp_project())
    app = FastAPI(
        title="Tasks API",
        version="0.1.0",
        separate_input_output_schemas=False,
        lifespan=lifespan,
    )
    app.router.redirect_slashes = False
    app.include_router(public.router)
    app.include_router(tenant.router)
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
def module_app() -> FastAPI:
    """The app `app` names, built on the first call and kept."""
    return create_app()


def __getattr__(name: str) -> FastAPI:
    """`app`, built the first time something reads it. Importing anything else from this
    module builds no app."""
    if name == "app":
        return module_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
