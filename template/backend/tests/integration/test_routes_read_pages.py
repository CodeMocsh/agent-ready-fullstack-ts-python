"""Every GET route reads a page. On a tenant that holds more than a page in each table, no read in
a request fetches more than `PAGE_MAX + 1` rows. `docs/adr/template/0026`.

The template owns this module. `tests/integration/filled.py` is the project's list: how it fills a
tenant, the path of each GET route in `openapi.json`, and who reads.
"""

import asyncio
from collections.abc import AsyncGenerator, Generator
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from typing import Any, Final
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.deps import PrincipalDep, admitted, database_of, resolved_principal
from app.identity.shared import Principal
from app.jobs import kinds
from app.main import create_app
from app.models import PAGE_MAX
from app.store import Database
from app.store.ddl import TENANT_TABLES
from app.store.jobs import Kinds
from app.store.pg import PostgresDatabase
from tests.integration.conftest import ONE_PROCESS, Provisioned
from tests.integration.filled import filled, reader
from tests.jobs_contract import contract_kinds

READ_ALONE: Final = frozenset({"/reads"})
"""GET routes the test does not read."""

PLANTED: Final = "/every-task"


class Counted:
    """A connection that adds the number of rows each read returns to `fetched`."""

    def __init__(self, conn: Any, fetched: list[int]) -> None:
        self._conn: Any = conn
        self._fetched: list[int] = fetched

    async def fetch(self, *args: Any, **kwargs: Any) -> list[Any]:
        rows: list[Any] = await self._conn.fetch(*args, **kwargs)
        self._fetched.append(len(rows))
        return rows

    async def fetchmany(self, *args: Any, **kwargs: Any) -> list[Any]:
        rows: list[Any] = await self._conn.fetchmany(*args, **kwargs)
        self._fetched.append(len(rows))
        return rows

    async def fetchrow(self, *args: Any, **kwargs: Any) -> Any:
        row = await self._conn.fetchrow(*args, **kwargs)
        self._fetched.append(0 if row is None else 1)
        return row

    async def fetchval(self, *args: Any, **kwargs: Any) -> Any:
        self._fetched.append(1)
        return await self._conn.fetchval(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


@pytest.fixture
def fetched(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The rows each read on a pooled connection returned, from now on."""
    counts: list[int] = []
    unmeasured = PostgresDatabase.connection

    @asynccontextmanager
    async def counted(self: PostgresDatabase) -> AsyncGenerator[Any]:
        connection: AbstractAsyncContextManager[Any] = unmeasured(self)
        async with connection as conn:
            yield Counted(conn, counts)

    monkeypatch.setattr(PostgresDatabase, "connection", counted)
    return counts


def with_contract_kinds(database: Database) -> Kinds:
    return {**kinds(database), **contract_kinds(database)}


async def fill(where: Provisioned, tenant_id: str) -> tuple[dict[str, str], dict[str, int]]:
    """The path of each GET route that `filled` answers, and each tenant table that holds no more
    than `PAGE_MAX + 1` rows of `tenant_id` after it, with its count."""
    database = PostgresDatabase(
        dsn=where.app_dsn, schema=where.schema, draws=ONE_PROCESS, kinds=with_contract_kinds
    )
    try:
        paths = await filled(database, tenant_id)
        short: dict[str, int] = {}
        for table in TENANT_TABLES:
            async with database.scoped(tenant_id) as conn:
                held: int = await conn.fetchval(f'SELECT count(*) FROM "{table}"')
            if held <= PAGE_MAX + 1:
                short[table] = held
        return paths, short
    finally:
        await database.close()


def read_by_get(app: FastAPI) -> frozenset[str]:
    """The template of each GET route in `openapi.json` that the test reads."""
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


def over_a_page(client: TestClient, paths: dict[str, str], fetched: list[int]) -> dict[str, Any]:
    """Each route whose read fetched more than `PAGE_MAX + 1` rows, with the rows of each read,
    and each route that did not answer `200`, with its status."""
    found: dict[str, Any] = {}
    for route, path in sorted(paths.items()):
        fetched.clear()
        answered = client.get(path)
        if answered.status_code != 200:
            found[route] = f"{path} answered {answered.status_code}: {answered.text}"
        elif max(fetched, default=0) > PAGE_MAX + 1:
            found[route] = list(fetched)
    return found


async def reads_every_task(request: Request, principal: PrincipalDep) -> int:
    database = database_of(request)
    assert isinstance(database, PostgresDatabase)
    async with database.scoped(principal.tenant_id) as conn:
        rows: list[Any] = await conn.fetch("SELECT id FROM tasks")
    return len(rows)


def test_each_get_route_fetches_at_most_a_page_for_each_read(
    provisioned: Provisioned, monkeypatch: pytest.MonkeyPatch, fetched: list[int]
) -> None:
    tenant_id = f"filled-{uuid4().hex}"
    paths, short = asyncio.run(fill(provisioned, tenant_id))
    assert short == {}, (
        f"tests/integration/filled.py leaves these tables at one page or less: {short}. "
        f"Write more than {PAGE_MAX + 1} rows of the tenant to each."
    )
    app = create_app()
    routes = read_by_get(app)
    assert set(paths) == routes, (
        f"tests/integration/filled.py names no path for {sorted(routes - set(paths))}, and "
        f"names {sorted(set(paths) - routes)}, which openapi.json has no GET route for"
    )

    with served(provisioned, tenant_id, monkeypatch, app) as client:
        assert over_a_page(client, paths, fetched) == {}


def test_a_route_that_reads_every_row_is_found(
    provisioned: Provisioned, monkeypatch: pytest.MonkeyPatch, fetched: list[int]
) -> None:
    tenant_id = f"filled-{uuid4().hex}"
    _ = asyncio.run(fill(provisioned, tenant_id))
    app = create_app()

    app.add_api_route(PLANTED, reads_every_task, methods=["GET"])

    with served(provisioned, tenant_id, monkeypatch, app) as client:
        found = over_a_page(client, {PLANTED: PLANTED}, fetched)

    assert list(found) == [PLANTED]
    assert max(found[PLANTED]) > PAGE_MAX + 1
