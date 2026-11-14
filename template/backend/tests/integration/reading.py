"""How the tests of what each GET route reads fill a tenant, serve the app, and count what each
request does on its pooled connections. `tests/integration/test_routes_read_pages.py` and
`tests/integration/test_routes_read_a_fixed_count.py` read each GET route with it."""

from collections.abc import AsyncGenerator, Generator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import Any, Final

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.deps import admitted, resolved_principal
from app.identity.shared import Principal
from app.jobs import kinds
from app.store import Database
from app.store.ddl import TENANT_TABLES
from app.store.jobs import Kinds
from app.store.pg import PostgresDatabase
from tests.integration.conftest import ONE_PROCESS, Provisioned
from tests.integration.filled import filled, reader
from tests.jobs_contract import contract_kinds

READ_ALONE: Final = frozenset({"/reads"})
"""GET routes the tests do not read."""


@dataclass
class Count:
    """What the requests did on pooled connections since the last `clear`: the rows that each
    read returned, each statement, each connection taken, and the most held at once."""

    fetched: list[int] = field(default_factory=list)
    statements: int = 0
    connections: int = 0
    held: int = 0
    most_held: int = 0

    def clear(self) -> None:
        self.fetched.clear()
        self.statements = self.connections = self.most_held = 0


class Counted:
    """A connection that adds each statement, and the rows that each read returns, to `count`."""

    def __init__(self, conn: Any, count: Count) -> None:
        self._conn: Any = conn
        self._count: Count = count

    async def fetch(self, *args: Any, **kwargs: Any) -> list[Any]:
        rows: list[Any] = await self._conn.fetch(*args, **kwargs)
        self._read(len(rows))
        return rows

    async def fetchmany(self, *args: Any, **kwargs: Any) -> list[Any]:
        rows: list[Any] = await self._conn.fetchmany(*args, **kwargs)
        self._read(len(rows))
        return rows

    async def fetchrow(self, *args: Any, **kwargs: Any) -> Any:
        row = await self._conn.fetchrow(*args, **kwargs)
        self._read(0 if row is None else 1)
        return row

    async def fetchval(self, *args: Any, **kwargs: Any) -> Any:
        self._read(1)
        return await self._conn.fetchval(*args, **kwargs)

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        self._count.statements += 1
        return await self._conn.execute(*args, **kwargs)

    async def executemany(self, *args: Any, **kwargs: Any) -> Any:
        self._count.statements += 1
        return await self._conn.executemany(*args, **kwargs)

    def _read(self, rows: int) -> None:
        self._count.fetched.append(rows)
        self._count.statements += 1

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


def counting(monkeypatch: pytest.MonkeyPatch) -> Count:
    """What each pooled connection does from now on."""
    counted = Count()
    unmeasured = PostgresDatabase.connection

    @asynccontextmanager
    async def connection(self: PostgresDatabase) -> AsyncGenerator[Any]:
        lent: AbstractAsyncContextManager[Any] = unmeasured(self)
        async with lent as conn:
            counted.connections += 1
            counted.held += 1
            counted.most_held = max(counted.most_held, counted.held)
            try:
                yield Counted(conn, counted)
            finally:
                counted.held -= 1

    monkeypatch.setattr(PostgresDatabase, "connection", connection)
    return counted


def with_contract_kinds(database: Database) -> Kinds:
    return {**kinds(database), **contract_kinds(database)}


async def fill(
    where: Provisioned, tenant_id: str, rows: int
) -> tuple[dict[str, str], dict[str, int]]:
    """The path of each GET route that `filled` answers for `rows`, and the rows of `tenant_id`
    that each tenant table holds after it."""
    database = PostgresDatabase(
        dsn=where.app_dsn, schema=where.schema, draws=ONE_PROCESS, kinds=with_contract_kinds
    )
    try:
        paths = await filled(database, tenant_id, rows)
        held: dict[str, int] = {}
        for table in TENANT_TABLES:
            async with database.scoped(tenant_id) as conn:
                held[table] = await conn.fetchval(f'SELECT count(*) FROM "{table}"')
        return paths, held
    finally:
        await database.close()


def read_by_get(app: FastAPI) -> frozenset[str]:
    """The template of each GET route in `openapi.json` that the tests read."""
    paths: dict[str, dict[str, Any]] = app.openapi()["paths"]
    return frozenset(path for path, methods in paths.items() if "get" in methods) - READ_ALONE


@contextmanager
def served(
    where: Provisioned, tenant_id: str, monkeypatch: pytest.MonkeyPatch, app: FastAPI
) -> Generator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", where.app_dsn)
    monkeypatch.setenv("DB_SCHEMA", where.schema)

    async def the_reader(request: Request) -> Principal:
        return admitted(request, reader(tenant_id))

    app.dependency_overrides[resolved_principal] = the_reader
    with TestClient(app) as client:
        yield client


def against_the_list[T](
    found: Mapping[str, T], listed: Mapping[str, str]
) -> tuple[dict[str, T], list[str]]:
    """Each route in `found` that `listed` does not name, with what was found, and each route
    that `listed` names and `found` does not."""
    return (
        {route: seen for route, seen in found.items() if route not in listed},
        sorted(set(listed) - set(found)),
    )
