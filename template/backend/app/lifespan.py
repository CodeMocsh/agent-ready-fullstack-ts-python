"""What this process runs beside the app: one `Database`, verified before the first request and
closed after the last, and the instruments that observe it.

`app/wiring.py` answers *what did this deployment configure*; this answers *what is this process
running*, and reads no variable. A `build_` function that reads none belongs here.
`docs/adr/0014`.

Verified, never applied: DDL is a release step (`make migrate`) and this process holds no
rights to it. A skipped release step is a failed startup rather than a failed request.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import log
from app.environment import refuse_development_settings
from app.identity import resolved_without_a_credential
from app.wiring import build, unauthenticated_is_acknowledged


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Refuse a production process carrying development settings, then hold the `Database` on
    `app.state` for the life of the process. Closes the database, then the instruments."""
    refuse_development_settings()
    database = build()
    version = await database.check()
    log.serving(database.name, version)
    await _say_what_this_deployment_authenticates(database.name)
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
                app.state.instruments.shutdown()


async def _say_what_this_deployment_authenticates(substrate: str) -> None:
    """Log whether the identity seam authenticates, at `WARNING` only when nobody has said that
    serving everybody is deliberate. `docs/adr/0008`."""
    tenant = await resolved_without_a_credential()
    if tenant is None:
        log.identity_verified()
    elif unauthenticated_is_acknowledged():
        log.identity_open_on_purpose(tenant)
    else:
        log.identity_open(tenant, substrate)
