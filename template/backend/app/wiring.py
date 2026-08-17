"""The one swap point: which substrate this process gets.

`build()` is where the environment is read, and it reads every variable this process uses
rather than letting them be picked up further down. That is what lets one process hold two
substrates at once — what the contract suite does — and what keeps a test from mutating
`os.environ` to choose one.

**No `DATABASE_URL` means the in-memory substrate**, so a fresh clone runs with no
infrastructure. That is a default, not a fallback: nothing here degrades from Postgres to
memory on an error, because a deployment that silently came up on memory has data that will
not be there tomorrow. A malformed `DATABASE_URL` fails at boot.

**And the application refuses to hold the owner credential.** Seeing `DATABASE_OWNER_URL` is
a refusal rather than a warning, for the reason `FORCE ROW LEVEL SECURITY` beats `ENABLE`: a
separation that depends on nobody making a mistake is not a separation. A single container
that migrates and then serves drops it between the two — `env -u DATABASE_OWNER_URL uvicorn`.
"""

import os
from typing import Final

from app.identity import SENTINEL_TENANT
from app.migrate import OWNER_URL_ENV
from app.store import Database
from app.store.conn import SCHEMA_ENV
from app.store.memory import MemoryDatabase

DATABASE_URL_ENV: Final = "DATABASE_URL"


class OwnerCredentialVisible(RuntimeError):
    """The application can see `DATABASE_OWNER_URL`, and it must not be able to."""


def build() -> Database:
    """The substrate this deployment gets, from the environment."""
    if os.environ.get(OWNER_URL_ENV):
        raise OwnerCredentialVisible(
            f"{OWNER_URL_ENV} is set in this process. The application never applies DDL, and "
            f"a web process holding a credential that can is the thing the two-role split "
            f"exists to prevent. Run `make migrate` as a release step and start the "
            f"application without it -- `env -u {OWNER_URL_ENV} ...` if they share a shell."
        )
    dsn = os.environ.get(DATABASE_URL_ENV)
    if dsn is None or dsn.strip() == "":
        return MemoryDatabase(seed_tenant=SENTINEL_TENANT)
    from app.store.pg import PostgresDatabase

    return PostgresDatabase(dsn=dsn, schema=os.environ.get(SCHEMA_ENV))
