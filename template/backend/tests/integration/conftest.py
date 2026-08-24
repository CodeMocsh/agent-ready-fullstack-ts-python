"""Fixtures for the tier in this directory. Everything here needs a real Postgres.

`make db-test` runs this folder. `norecursedirs` keeps it out of the default run, so
`make test` needs no daemon, and nothing below has to decide for itself whether to run.

`TEST_DATABASE_URL` says where the server is. `make db-test` starts a throwaway container and
sets it; set it yourself to use your own server instead. Unset, the fixtures here raise: by
the time one runs, the run has already asked for the database.

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

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest

from app.store import Database
from app.store.conn import resolve_schema
from app.store.migrate import apply
from app.store.roles import app_role, emit_roles_sql, owner_role

TEST_DATABASE_URL = "TEST_DATABASE_URL"

TEST_PASSWORD = "isolation-suite"
"""For a throwaway role on a throwaway container. Nothing outside a test ever sees it."""


def postgres_dsn() -> str:
    """The test database, or an error. Never a default and never a skip -- a silent fallback
    to memory would make the Postgres half of the contract pass without touching Postgres,
    and a skip would make a run that tested nothing look like a run that passed."""
    dsn = os.environ.get(TEST_DATABASE_URL, "").strip()
    if not dsn:
        raise RuntimeError(
            f"{TEST_DATABASE_URL} is unset, and this suite needs a real Postgres. "
            f"Run `make db-test`, which starts one and sets it, or set it yourself."
        )
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
    """A schema, its two roles, and the DDL applied, dropped afterwards.

    The only fixture that provisions. Its `app_dsn` is what a deployment would put in
    `DATABASE_URL`, so a test that wants a deployment takes this and reads that field.
    """
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
async def database(provisioned: Provisioned) -> AsyncIterator[Database]:
    """A `PostgresDatabase` on that schema, connected as the application role.

    Named for what the contract in `tests/store_contract.py` asks for, so the suite that runs
    it here needs nothing but the subclass.
    """
    postgres = await a_postgres_database(provisioned)
    try:
        yield postgres
    finally:
        await postgres.close()
