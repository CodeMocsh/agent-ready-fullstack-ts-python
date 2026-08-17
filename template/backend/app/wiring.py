"""The one swap point: which substrate this process gets.

`build()` is where the environment is read, and it reads all three variables here rather than
letting them be picked up further down: `resolve_schema` and `resolve_mode` keep their own
fallbacks for a substrate constructed directly, and nothing on this path relies on them. That
is what lets one process hold two substrates at once — what the contract suite does — and what
keeps a test from mutating `os.environ` to choose one.

**No `DATABASE_URL` means the in-memory substrate**, so a fresh clone runs with no
infrastructure. That is a default, not a fallback: nothing here degrades from Postgres to
memory on an error, because a deployment that silently came up on memory has data that will
not be there tomorrow. A malformed `DATABASE_URL` fails at boot.
"""

import os
from typing import Final

from app.store import Database
from app.store.conn import SCHEMA_ENV
from app.store.memory import MemoryDatabase
from app.store.migrate import MIGRATE_ENV

DATABASE_URL_ENV: Final = "DATABASE_URL"


def build() -> Database:
    """The substrate this deployment gets, from the environment."""
    dsn = os.environ.get(DATABASE_URL_ENV)
    if dsn is None or dsn.strip() == "":
        return MemoryDatabase()
    from app.store.pg import PostgresDatabase

    return PostgresDatabase(
        dsn=dsn,
        schema=os.environ.get(SCHEMA_ENV),
        mode=os.environ.get(MIGRATE_ENV),
    )
