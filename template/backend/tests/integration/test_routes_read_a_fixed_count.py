"""A GET route's reads do not grow with its rows, and a request holds one connection at a time.
Each GET route reads a tenant of one row with as many statements and connections as a tenant past
a page. `docs/adr/template/0032`.

The template owns this module. `tests/integration/filled.py` is the project's list: how it fills a
tenant, the path of each GET route, each route whose reads may grow with its rows, and each route
that may hold more than one connection at once, until it does not.
"""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request

from app.deps import PrincipalDep, database_of
from app.main import create_app
from app.models import PAGE_MAX, TaskPage
from app.store.pg import PostgresDatabase
from tests.integration.conftest import Provisioned
from tests.integration.filled import (
    GROWS_WITH_ROWS,
    MORE_THAN_ONE_CONNECTION,
    NOT_READ_YET,
    ROWS,
)
from tests.integration.reading import (
    Count,
    against_the_list,
    counting,
    fill,
    read_by_get,
    served,
)

EACH_ALONE: Final = "/each-task-alone"
EACH_AT_ONCE: Final = "/each-task-at-once"
EACH_LOADED: Final = "/each-task-loaded"


@dataclass(frozen=True)
class Reads:
    """What one request did on pooled connections."""

    statements: int
    connections: int
    most_held: int


@pytest.fixture
def count(monkeypatch: pytest.MonkeyPatch) -> Count:
    return counting(monkeypatch)


def reads_of(
    where: Provisioned,
    tenant_id: str,
    monkeypatch: pytest.MonkeyPatch,
    app: FastAPI,
    paths: Mapping[str, str],
    count: Count,
) -> dict[str, Reads]:
    """What one request of each route in `paths` did in `tenant_id`. Fails on a path that does
    not answer `200`."""
    found: dict[str, Reads] = {}
    with served(where, tenant_id, monkeypatch, app) as client:
        for route, path in sorted(paths.items()):
            count.clear()
            answered = client.get(path)
            assert answered.status_code == 200, (
                f"{path} answered {answered.status_code} for {route}: {answered.text}"
            )
            found[route] = Reads(count.statements, count.connections, count.most_held)
    return found


def grown(one: Mapping[str, Reads], page: Mapping[str, Reads]) -> dict[str, tuple[Reads, Reads]]:
    """Each route that made more statements or took more connections in `page` than in `one`,
    with both."""
    return {
        route: (one[route], read)
        for route, read in page.items()
        if (read.statements, read.connections) != (one[route].statements, one[route].connections)
    }


def held_at_once(*reads: Mapping[str, Reads]) -> dict[str, int]:
    """Each route that held more than one connection at once in any of `reads`, with the most."""
    most: dict[str, int] = {}
    for found in reads:
        for route, read in found.items():
            if read.most_held > 1:
                most[route] = max(most.get(route, 0), read.most_held)
    return most


def tenants(
    where: Provisioned,
) -> tuple[tuple[str, dict[str, str]], tuple[str, dict[str, str]]]:
    """A tenant of one row and a tenant past a page, each with the path of each GET route."""
    one, page = f"one-{uuid4().hex}", f"page-{uuid4().hex}"
    one_paths, one_held = asyncio.run(fill(where, one, 1))
    page_paths, _ = asyncio.run(fill(where, page, ROWS))
    full = {table: rows for table, rows in one_held.items() if rows >= PAGE_MAX}
    assert full == {}, (
        f"filled with one row left {full} rows in these tables, a page or more. Write as many "
        f"rows as filled is asked for."
    )
    assert set(one_paths) == set(page_paths), (
        f"filled answers paths for {sorted(one_paths)} at one row and for {sorted(page_paths)} "
        f"past a page. Answer a path for the same routes at each."
    )
    return (one, one_paths), (page, page_paths)


def tenant_of(request: Request, principal: PrincipalDep) -> tuple[PostgresDatabase, str]:
    database = database_of(request)
    assert isinstance(database, PostgresDatabase)
    return database, principal.tenant_id


async def first_page(database: PostgresDatabase, tenant_id: str) -> TaskPage:
    page = await database.store(tenant_id).list(None)
    assert page is not None
    return page


async def titles(conn: Any, ids: Sequence[UUID]) -> Mapping[UUID, str]:
    rows: list[Any] = await conn.fetch("SELECT id, title FROM tasks WHERE id = ANY($1)", ids)
    return {row["id"]: row["title"] for row in rows}


async def title_alone(database: PostgresDatabase, tenant_id: str, id: str) -> str:
    async with database.scoped(tenant_id) as conn:
        title: str = await conn.fetchval("SELECT title FROM tasks WHERE id = $1", UUID(id))
    return title


async def each_task_alone(request: Request, principal: PrincipalDep) -> list[str]:
    database, tenant_id = tenant_of(request, principal)
    page = await first_page(database, tenant_id)
    return [await title_alone(database, tenant_id, task.id) for task in page.tasks]


async def each_task_at_once(request: Request, principal: PrincipalDep) -> list[str]:
    database, tenant_id = tenant_of(request, principal)
    page = await first_page(database, tenant_id)
    async with asyncio.TaskGroup() as group:
        read = [group.create_task(title_alone(database, tenant_id, task.id)) for task in page.tasks]
    return [task.result() for task in read]


async def each_task_loaded(request: Request, principal: PrincipalDep) -> list[str]:
    database, tenant_id = tenant_of(request, principal)
    page = await first_page(database, tenant_id)
    loader = database.loader(tenant_id)
    async with asyncio.TaskGroup() as group:
        read = [group.create_task(loader.load(titles, UUID(task.id))) for task in page.tasks]
    return [task.result() for task in read]


def test_each_get_route_reads_as_much_at_one_row_as_past_a_page(
    provisioned: Provisioned, monkeypatch: pytest.MonkeyPatch, count: Count
) -> None:
    (one, one_paths), (page, page_paths) = tenants(provisioned)
    app = create_app()
    routes = read_by_get(app) - set(NOT_READ_YET)
    for named, listed in (
        ("GROWS_WITH_ROWS", GROWS_WITH_ROWS),
        ("MORE_THAN_ONE_CONNECTION", MORE_THAN_ONE_CONNECTION),
    ):
        assert sorted(set(listed) - routes) == [], (
            f"{named} in tests/integration/filled.py names {sorted(set(listed) - routes)}, which "
            f"is no GET route that this test reads"
        )
    read = {route: path for route, path in one_paths.items() if route in routes}
    at_one = reads_of(provisioned, one, monkeypatch, app, read, count)
    past_a_page = reads_of(
        provisioned,
        page,
        monkeypatch,
        app,
        {route: page_paths[route] for route in read},
        count,
    )

    grew, grew_in_vain = against_the_list(grown(at_one, past_a_page), GROWS_WITH_ROWS)
    held, held_in_vain = against_the_list(
        held_at_once(at_one, past_a_page), MORE_THAN_ONE_CONNECTION
    )

    assert grew == {}, (
        f"Each of these GET routes made more statements or took more connections at {ROWS} rows "
        f"than at one, as (at one, at {ROWS}): {grew}. Read each kind of fact for every item of "
        f"a page in one set read, through PostgresDatabase.loader, or name the route in "
        f"GROWS_WITH_ROWS in tests/integration/filled.py, with its reason."
    )
    assert held == {}, (
        f"Each of these GET routes held more than one connection at once: {held}. Read through "
        f"PostgresDatabase.loader, or name the route in MORE_THAN_ONE_CONNECTION in "
        f"tests/integration/filled.py, with its reason."
    )
    assert grew_in_vain == [], (
        f"GROWS_WITH_ROWS in tests/integration/filled.py names {grew_in_vain}. Each read as much "
        f"at one row as at {ROWS}. Remove each from the list."
    )
    assert held_in_vain == [], (
        f"MORE_THAN_ONE_CONNECTION in tests/integration/filled.py names {held_in_vain}. Each held "
        f"one connection at a time. Remove each from the list."
    )


@pytest.mark.parametrize(
    ("named", "listed"),
    [
        ("GROWS_WITH_ROWS", GROWS_WITH_ROWS),
        ("MORE_THAN_ONE_CONNECTION", MORE_THAN_ONE_CONNECTION),
    ],
)
def test_each_entry_of_a_list_names_its_reason(named: str, listed: Mapping[str, str]) -> None:
    unreasoned = [entry for entry, reason in listed.items() if reason.strip() == ""]
    assert unreasoned == [], (
        f"{named} in tests/integration/filled.py gives {unreasoned} an empty reason. Write the "
        f"reason."
    )


def test_a_read_for_each_item_is_found_and_a_loader_reads_each_kind_once(
    provisioned: Provisioned, monkeypatch: pytest.MonkeyPatch, count: Count
) -> None:
    (one, _), (page, _) = tenants(provisioned)
    app = create_app()
    planted = {EACH_ALONE: each_task_alone, EACH_AT_ONCE: each_task_at_once}
    for path, route in {**planted, EACH_LOADED: each_task_loaded}.items():
        app.add_api_route(path, route, methods=["GET"])
    paths = {path: path for path in (EACH_ALONE, EACH_AT_ONCE, EACH_LOADED)}

    at_one = reads_of(provisioned, one, monkeypatch, app, paths, count)
    past_a_page = reads_of(provisioned, page, monkeypatch, app, paths, count)

    assert sorted(grown(at_one, past_a_page)) == sorted(planted)
    assert list(held_at_once(at_one, past_a_page)) == [EACH_AT_ONCE]
    assert past_a_page[EACH_LOADED] == at_one[EACH_LOADED]
    assert past_a_page[EACH_LOADED].most_held == 1
    assert past_a_page[EACH_ALONE].statements - at_one[EACH_ALONE].statements >= PAGE_MAX - 1
