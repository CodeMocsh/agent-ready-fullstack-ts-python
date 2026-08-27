import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.identity import Unauthenticated, resolved_without_a_credential
from app.models import ErrorBody
from app.routes import public_router, router
from app.wiring import ACKNOWLEDGED_ENV, build, unauthenticated_is_acknowledged

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
    await _say_what_this_deployment_authenticates(database.name)
    app.state.database = database
    try:
        yield
    finally:
        await database.close()


async def _say_what_this_deployment_authenticates(substrate: str) -> None:
    """State it at every boot, and raise your voice only when nobody has said it on purpose.

    The fact is logged either way and only the level moves; `docs/adr/0008` says why a warning
    a deployment cannot acknowledge is one it learns to mute.
    """
    tenant = await resolved_without_a_credential()
    if tenant is None:
        _LOG.info("identity: a credential is verified on every request")
        return
    if unauthenticated_is_acknowledged():
        _LOG.info(
            "identity: none, and %s says that is deliberate -- every request is served as "
            "tenant %r",
            ACKNOWLEDGED_ENV,
            tenant,
        )
        return
    _LOG.warning(
        "identity: this deployment authenticates nothing. A request carrying no credential "
        "resolved to tenant %r, so anyone who can reach this process can read and write "
        "everything it holds on the %s substrate. Replace tenant_for() in app/identity.py -- "
        "or set %s=1 to record that serving everybody is deliberate and see this as INFO.",
        tenant,
        substrate,
        ACKNOWLEDGED_ENV,
    )


def create_app() -> FastAPI:
    """The app, assembled. A function so a test can hold two with different substrates.

    `docs/adr/0007` holds the settings below and why each one is off.
    """
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


app = create_app()
