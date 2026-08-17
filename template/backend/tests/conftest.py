"""Fixtures shared by the suites, and the one place a test learns whether Postgres is here.

`TEST_DATABASE_URL` is the switch. Unset, the Postgres members **skip** rather than fail, so
`make test` stays hermetic on a laptop with no daemon. Set, they run — and `make db-test`
starts a throwaway container and sets it.

A skip is the dangerous half of that arrangement: a run where every Postgres member skipped
exits 0 and looks exactly like one where every one passed. `make db-test` runs pytest under
`-rs` so the skips are printed, and `make test` says which tier it is.

**Every Postgres test gets its own schema, and its own pair of roles.** Not a shared one
truncated between tests: a unique schema is what lets several of these run at once -- several
agents work this repository in parallel -- and it makes each test start from an empty table.
Role names derive from the schema, so the roles are unique too.

**And the substrate fixtures connect as the application role, never as the administrator.** A
superuser bypasses row-level security regardless of `FORCE`, so a suite wired to the admin
connection would assert tenant isolation against a database where none was in force and pass.
The schema is applied by the admin connection, exactly as a release step does; everything
after that is the least-privilege role the deployment actually runs as.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest

from app.identity import SENTINEL_TENANT
from app.store import Database
from app.store.conn import resolve_schema
from app.store.memory import MemoryDatabase
from app.store.migrate import apply
from app.store.roles import app_role, emit_roles_sql, owner_role

TEST_DATABASE_URL = "TEST_DATABASE_URL"

SUBSTRATES = ("memory", "postgres")

TEST_PASSWORD = "isolation-suite"
"""For a throwaway role on a throwaway container. Nothing outside a test ever sees it."""


def postgres_dsn() -> str:
    """The test database, or a skip. Never a default -- a silent fallback to memory would
    make the Postgres half of every contract test pass without touching Postgres."""
    dsn = os.environ.get(TEST_DATABASE_URL)
    if dsn is None or dsn.strip() == "":
        pytest.skip(f"{TEST_DATABASE_URL} is unset; run `make db-test`")
    return dsn


def a_fresh_schema_name() -> str:
    return resolve_schema(f"test_{uuid4().hex[:12]}")


async def connect(dsn: str) -> Any:
    import asyncpg

    return await asyncpg.connect(dsn=dsn)


async def drop_schema(dsn: str, schema: str) -> None:
    conn: Any = await connect(dsn)
    try:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    finally:
        await conn.close()


async def apply_schema(dsn: str, schema: str) -> str:
    """Apply the DDL the way a release does: a separate, privileged, one-off connection.

    Never through `Database`. The application cannot apply DDL and the fixtures must not
    pretend otherwise, or the suite would be testing a deployment nobody runs.
    """
    conn: Any = await connect(dsn)
    try:
        return await apply(conn, schema)
    finally:
        await conn.close()


def as_role(dsn: str, role: str, password: str) -> str:
    """The same server, as a different role."""
    parts = urlsplit(dsn)
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{role}:{password}@{parts.hostname or 'localhost'}{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


@dataclass(frozen=True)
class Provisioned:
    """A schema with its two roles, and a connection string for each."""

    admin_dsn: str
    app_dsn: str
    schema: str
    owner: str
    app: str


async def provision(admin_dsn: str, schema: str) -> Provisioned:
    """Roles, grants and schema, exactly as `deploy/roles.sql` then `make migrate` would.

    Role names derive from the schema, so every test in this suite gets its own pair and the
    file can run in parallel with itself -- which a cluster-wide fixed name could not.
    """
    conn: Any = await connect(admin_dsn)
    try:
        await conn.execute(emit_roles_sql(schema))
        await conn.execute(f"ALTER ROLE \"{app_role(schema)}\" PASSWORD '{TEST_PASSWORD}'")
    finally:
        await conn.close()
    await apply_schema(admin_dsn, schema)
    return Provisioned(
        admin_dsn=admin_dsn,
        app_dsn=as_role(admin_dsn, app_role(schema), TEST_PASSWORD),
        schema=schema,
        owner=owner_role(schema),
        app=app_role(schema),
    )


async def deprovision(admin_dsn: str, schema: str) -> None:
    conn: Any = await connect(admin_dsn)
    try:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        for role in (app_role(schema), owner_role(schema)):
            await conn.execute(
                "DO $t$\nBEGIN\n"
                f"  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN\n"
                f'    DROP OWNED BY "{role}" CASCADE;\n'
                f'    DROP ROLE "{role}";\n'
                "  END IF;\nEND\n$t$"
            )
    finally:
        await conn.close()


@pytest.fixture
async def provisioned() -> AsyncIterator[Provisioned]:
    """A schema, its two roles, and the DDL applied. What the isolation suite runs against."""
    dsn = postgres_dsn()
    schema = a_fresh_schema_name()
    try:
        yield await provision(dsn, schema)
    finally:
        await deprovision(dsn, schema)


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
async def pg_deployment() -> AsyncIterator[Provisioned]:
    """A provisioned schema whose `app_dsn` is what a deployment would put in `DATABASE_URL`."""
    dsn = postgres_dsn()
    schema = a_fresh_schema_name()
    try:
        yield await provision(dsn, schema)
    finally:
        await deprovision(dsn, schema)


async def a_postgres_database(where: Provisioned) -> Any:
    from app.store.pg import PostgresDatabase

    database = PostgresDatabase(dsn=where.app_dsn, schema=where.schema)
    await database.check()
    return database


@pytest.fixture
async def pg_database() -> AsyncIterator[Database]:
    """A `PostgresDatabase` on a schema of its own, connected as the application role."""
    dsn = postgres_dsn()
    schema = a_fresh_schema_name()
    where = await provision(dsn, schema)
    database = await a_postgres_database(where)
    try:
        yield database
    finally:
        await database.close()
        await deprovision(dsn, schema)


@pytest.fixture(params=SUBSTRATES)
async def database(request: pytest.FixtureRequest) -> AsyncIterator[Database]:
    """Both substrates, one at a time. What makes `TaskStore` a contract and not a claim."""
    if request.param == "memory":
        memory = MemoryDatabase(seed_tenant=SENTINEL_TENANT)
        yield memory
        await memory.close()
        return
    dsn = postgres_dsn()
    schema = a_fresh_schema_name()
    where = await provision(dsn, schema)
    postgres = await a_postgres_database(where)
    try:
        yield postgres
    finally:
        await postgres.close()
        await deprovision(dsn, schema)
