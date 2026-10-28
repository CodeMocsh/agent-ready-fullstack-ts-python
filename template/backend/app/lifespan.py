"""What this process runs beside the app: the routes `build_routes` names, built before the first
request; one `Database`, verified before the first request and closed after the last; and the
instruments that observe it.

`app/wiring.py` answers *what did this deployment configure*; this answers *what is this process
running*, and reads no variable. A `build_` function that reads none belongs here.
`docs/adr/template/0010`.

Verified, never applied: DDL is a release step (`make migrate`) and this process holds no
rights to it. A skipped release step is a failed startup rather than a failed request.
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.routing import RouteContext, iter_route_contexts

from app import log
from app.environment import refuse_development_settings
from app.identity import principal_for
from app.identity.shared import resolved_without_a_credential
from app.wiring import build, unauthenticated_is_acknowledged


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Refuse a production process carrying development settings, build the routes, then hold
    the `Database` on `app.state` for the life of the process. Closes the database, then the
    instruments."""
    refuse_development_settings()
    _ = await asyncio.to_thread(build_routes, app)
    database = build()
    version = await database.check()
    log.serving(database.name, version)
    await _say_what_this_deployment_authenticates(app, database.name)
    app.state.database = database
    if app.state.instruments is not None:
        app.state.instruments.observe(database)
    try:
        yield
    finally:
        try:
            await database.close()
        finally:
            if app.state.instruments is not None:
                await asyncio.to_thread(app.state.instruments.shutdown)


def build_routes(app: FastAPI) -> list[RouteContext]:
    """Every route that `iter_route_contexts` yields for `app`, built, so that no request builds
    one. A route that `frontend()` adds to an included router is not among them: the first request
    that no other route matches builds it. Blocks: call it off the event loop."""
    return list(iter_route_contexts(app.routes))


async def _say_what_this_deployment_authenticates(app: FastAPI, substrate: str) -> None:
    """Log whether the identity seam `app` serves with authenticates, at `WARNING` only when nobody
    has said that serving everybody is deliberate. The seam is `principal_for`, or what
    `app.dependency_overrides` puts in its place. `docs/adr/template/0004`."""
    resolver = app.dependency_overrides.get(principal_for, principal_for)
    principal = await resolved_without_a_credential(resolver)
    if principal is None:
        log.identity_verified()
    elif unauthenticated_is_acknowledged():
        log.identity_open_on_purpose(principal.tenant_id)
    else:
        log.identity_open(principal.tenant_id, substrate)
