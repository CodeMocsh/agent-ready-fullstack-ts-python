from functools import cache
from typing import TYPE_CHECKING, Final

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.types import Lifespan

from app import log, request_events, waits
from app.identity.shared import Unauthenticated
from app.lifespan import lifespan
from app.models import ErrorBody
from app.routes import public, tenant
from app.wiring import TelemetrySettings, build_service_version, build_telemetry

if TYPE_CHECKING:
    from app.telemetry import Instruments

ROUTERS: Final = (public.router, tenant.router)
"""Every router the app serves: one for each requirement in `app/routes/`."""


def create_app() -> FastAPI:
    """The app, assembled. A function so a test can hold two with different substrates.

    `docs/adr/template/0008` holds the settings `assemble` makes and why each one is off.
    Building one configures the logging of the whole process: see `app.log.configure`.
    """
    return assemble(*ROUTERS, lifespan=lifespan)


def assemble(*routers: APIRouter, lifespan: Lifespan[FastAPI] | None) -> FastAPI:
    """An app that serves `routers` and runs `lifespan`, with the refusal of an unresolved tenant,
    the mark `app.waits.waiting` reads, the request event, the request line and the telemetry
    around each route. `create_app` and `tests.routed.routed` build with it. Configures the
    logging of the whole process."""
    log.configure(build_service_version())
    app = FastAPI(
        title="Tasks API",
        version="0.1.0",
        separate_input_output_schemas=False,
        lifespan=lifespan,
    )
    app.router.redirect_slashes = False
    for router in routers:
        app.include_router(router)
    app.add_exception_handler(Unauthenticated, _refuse)
    waits.instrument(app)
    request_events.instrument(app)
    log.instrument(app)
    settings = build_telemetry()
    app.state.instruments = None if settings is None else _instrument(app, settings)
    return app


def _instrument(app: FastAPI, settings: TelemetrySettings) -> "Instruments":
    """`app` instrumented, exporting to the Collector `settings` names.

    Imports `app.telemetry` here, so a process without telemetry loads no exporter and no
    instrumentation: `docs/deployment.md#a-cold-start` says what each loaded file costs.
    """
    from app import telemetry

    return telemetry.instrument(app, settings, *telemetry.otlp(settings))


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
