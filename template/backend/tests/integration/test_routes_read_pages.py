"""Every GET route reads a page. On a tenant that holds more than a page in each table, no read in
a request fetches more than `PAGE_MAX + 1` rows. `docs/adr/template/0026`.

The template owns this module. `tests/integration/filled.py` is the project's list: how it fills a
tenant, the path of each GET route in `openapi.json`, who reads, each route that may fetch more
than a page until it reads a page, and each table and route that it does not fill or read yet.
"""

import asyncio
from collections.abc import AsyncGenerator, Generator, Mapping
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
from tests.integration.filled import NOT_FILLED_YET, NOT_READ_YET, OVER_A_PAGE, filled, reader
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


def gaps(
    short: Mapping[str, int],
    paths: Mapping[str, str],
    every_get: frozenset[str],
    not_filled: Mapping[str, str],
    not_read: Mapping[str, str],
) -> dict[str, list[str]]:
    """Each way that `tests/integration/filled.py` and its two lists disagree with the tables it
    left at one page or less, `short`, and with each GET route the test reads, `every_get`. Each
    key says what to do."""
    found = {
        f"Write more than {PAGE_MAX + 1} rows of the tenant to each table, or name it in "
        "NOT_FILLED_YET with its reason": set(short) - set(not_filled),
        "Remove each table from NOT_FILLED_YET: it is filled, or is no tenant table": set(
            not_filled
        )
        - set(short),
        "Remove each route from NOT_READ_YET: it is no GET route that this test reads": set(
            not_read
        )
        - every_get,
        "Answer a path for each route, or name it in NOT_READ_YET with its reason": every_get
        - set(not_read)
        - set(paths),
        "Remove each route from NOT_READ_YET: filled answers its path": set(paths) & set(not_read),
        "Answer no path for each: openapi.json has no GET route for it": set(paths) - every_get,
    }
    return {problem: sorted(names) for problem, names in found.items() if names}


def over_a_page(
    client: TestClient, paths: dict[str, str], fetched: list[int]
) -> dict[str, list[int]]:
    """Each route whose read fetched more than `PAGE_MAX + 1` rows, with the rows of each read.
    Fails on a path that does not answer `200`."""
    found: dict[str, list[int]] = {}
    for route, path in sorted(paths.items()):
        fetched.clear()
        answered = client.get(path)
        assert answered.status_code == 200, (
            f"{path} answered {answered.status_code} for {route}: {answered.text}"
        )
        if max(fetched, default=0) > PAGE_MAX + 1:
            found[route] = list(fetched)
    return found


def against_the_list(
    found: dict[str, list[int]], listed: Mapping[str, str]
) -> tuple[dict[str, list[int]], list[str]]:
    """Each route in `found` that `listed` does not name, with its rows, and each route that
    `listed` names and `found` does not."""
    return (
        {route: rows for route, rows in found.items() if route not in listed},
        sorted(set(listed) - set(found)),
    )


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
    app = create_app()
    every_get = read_by_get(app)
    found = gaps(short, paths, every_get, NOT_FILLED_YET, NOT_READ_YET)
    assert found == {}, f"tests/integration/filled.py disagrees with the app: {found}"
    routes = every_get - set(NOT_READ_YET)
    assert sorted(set(OVER_A_PAGE) - routes) == [], (
        f"OVER_A_PAGE in tests/integration/filled.py names "
        f"{sorted(set(OVER_A_PAGE) - routes)}, which is no GET route that this test reads"
    )

    with served(provisioned, tenant_id, monkeypatch, app) as client:
        unlisted, listed_in_vain = against_the_list(
            over_a_page(client, paths, fetched), OVER_A_PAGE
        )

    assert unlisted == {}, (
        f"A read of each of these GET routes fetched more than {PAGE_MAX + 1} rows: {unlisted}. "
        f"Read a page, or name the route in OVER_A_PAGE in tests/integration/filled.py, with "
        f"its reason."
    )
    assert listed_in_vain == [], (
        f"OVER_A_PAGE in tests/integration/filled.py names {listed_in_vain}. No read of these "
        f"routes fetched more than {PAGE_MAX + 1} rows. Remove each from the list."
    )


@pytest.mark.parametrize(
    ("named", "listed"),
    [
        ("OVER_A_PAGE", OVER_A_PAGE),
        ("NOT_FILLED_YET", NOT_FILLED_YET),
        ("NOT_READ_YET", NOT_READ_YET),
    ],
)
def test_each_entry_of_a_list_names_its_reason(named: str, listed: Mapping[str, str]) -> None:
    unreasoned = [entry for entry, reason in listed.items() if reason.strip() == ""]
    assert unreasoned == [], (
        f"{named} in tests/integration/filled.py gives {unreasoned} an empty reason. Write the "
        f"reason."
    )


def test_a_route_that_reads_every_row_is_found_unless_the_list_names_it(
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
    assert against_the_list(found, {}) == (found, [])
    assert against_the_list(found, {PLANTED: "reads every task"}) == ({}, [])
    assert against_the_list({}, {PLANTED: "reads every task"}) == ({}, [PLANTED])


def test_each_gap_between_the_lists_and_the_app_is_named() -> None:
    every_get = frozenset({"/a", "/b", "/c"})
    paths = {"/a": "/a", "/b": "/b"}

    assert gaps({}, paths, every_get, {}, {"/c": "reads a document"}) == {}
    assert gaps({"t": 3}, paths, every_get, {"t": "no store yet"}, {"/c": "x"}) == {}
    found = gaps({"t": 3}, {**paths, "/c": "/c", "/d": "/d"}, every_get, {"u": "x"}, {"/c": "x"})
    assert sorted(found.values()) == [["/c"], ["/d"], ["t"], ["u"]]
    assert sorted(gaps({}, paths, every_get, {}, {"/e": "x"}).values()) == [["/c"], ["/e"]]
