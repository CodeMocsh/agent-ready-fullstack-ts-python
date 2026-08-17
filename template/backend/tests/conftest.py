"""Fixtures shared by the suites, and the one place a test learns whether Postgres is here.

`TEST_DATABASE_URL` is the switch. Unset, the Postgres members **skip** rather than fail, so
`make test` stays hermetic on a laptop with no daemon. Set, they run — and `make db-test`
starts a throwaway container and sets it.

A skip is the dangerous half of that arrangement: a run where every Postgres member skipped
exits 0 and looks exactly like one where every one passed. `make db-test` runs pytest under
`-rs` so the skips are printed, and `make test` says which tier it is.

**Every Postgres test gets its own schema.** Not a shared one that is truncated between
tests: a unique schema is what lets several of these run at once -- several agents work this
repository in parallel -- and it makes each test start from an empty table rather than from
whatever the last one left.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

import pytest

from app.store import Database
from app.store.conn import resolve_schema
from app.store.memory import MemoryDatabase

TEST_DATABASE_URL = "TEST_DATABASE_URL"

SUBSTRATES = ("memory", "postgres")


def postgres_dsn() -> str:
    """The test database, or a skip. Never a default -- a silent fallback to memory would
    make the Postgres half of every contract test pass without touching Postgres."""
    dsn = os.environ.get(TEST_DATABASE_URL)
    if dsn is None or dsn.strip() == "":
        pytest.skip(f"{TEST_DATABASE_URL} is unset; run `make db-test`")
    return dsn


def a_fresh_schema_name() -> str:
    return resolve_schema(f"test_{uuid4().hex[:12]}")


async def drop_schema(dsn: str, schema: str) -> None:
    import asyncpg

    conn: Any = await asyncpg.connect(dsn=dsn)
    try:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    finally:
        await conn.close()


@pytest.fixture
def pg_schema() -> Iterator[tuple[str, str]]:
    """A DSN and an unused schema name, dropped afterwards. For the sync suites."""
    dsn = postgres_dsn()
    schema = a_fresh_schema_name()
    try:
        yield dsn, schema
    finally:
        asyncio.run(drop_schema(dsn, schema))


@pytest.fixture
async def pg_database() -> AsyncIterator[Database]:
    """A migrated `PostgresDatabase` on a schema of its own."""
    from app.store.pg import PostgresDatabase

    dsn = postgres_dsn()
    schema = a_fresh_schema_name()
    database = PostgresDatabase(dsn=dsn, schema=schema)
    await database.migrate()
    try:
        yield database
    finally:
        await database.close()
        await drop_schema(dsn, schema)


@pytest.fixture(params=SUBSTRATES)
async def database(request: pytest.FixtureRequest) -> AsyncIterator[Database]:
    """Both substrates, one at a time. What makes `TaskStore` a contract and not a claim."""
    if request.param == "memory":
        memory = MemoryDatabase()
        yield memory
        await memory.close()
        return
    from app.store.pg import PostgresDatabase

    dsn = postgres_dsn()
    schema = a_fresh_schema_name()
    postgres = PostgresDatabase(dsn=dsn, schema=schema)
    await postgres.migrate()
    try:
        yield postgres
    finally:
        await postgres.close()
        await drop_schema(dsn, schema)
