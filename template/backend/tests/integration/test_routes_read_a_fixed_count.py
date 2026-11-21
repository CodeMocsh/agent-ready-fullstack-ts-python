"""A GET route's reads do not grow with its rows, a request holds one connection at a time, and
a request sends each statement once. No GET route makes more statements or takes more
connections in a tenant past a page than in a tenant of one row. `docs/adr/template/0032` and
`0033`.

The template owns this module. `tests/integration/filled.py` is the project's list: how it fills a
tenant, the path of each GET route, each route whose reads may grow with its rows, each route
that may hold more than one connection at once, and each route that may send one statement more
than once, until it does not.
"""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request

from app.deps import PrincipalDep, database_of
from app.main import create_app
from app.models import PAGE_MAX, TaskPage
from app.store.pg import PostgresDatabase
from app.waits import SharedWork, beside
from tests.integration.conftest import Provisioned
from tests.integration.filled import (
    GROWS_WITH_ROWS,
    MORE_THAN_ONE_CONNECTION,
    NOT_FILLED_YET,
    NOT_READ_YET,
    REPEATS_A_STATEMENT,
    ROWS,
)
from tests.integration.reading import (
    Count,
    against_the_list,
    counting,
    fill,
    read_by_get,
    served,
    short_of,
)

EACH_ALONE: Final = "/each-task-alone"
EACH_AT_ONCE: Final = "/each-task-at-once"
EACH_LOADED: Final = "/each-task-loaded"
READS_ONCE: Final = "/reads-the-first-page"
READS_TWICE: Final = "/reads-the-first-page-twice"
WRITES_WHILE_READING: Final = "/writes-while-reading"
WHILE_OTHERS_HOLD: Final = "/while-others-hold"


@dataclass(frozen=True)
class Reads:
    """What one request did on pooled connections, and the code that sent each statement it
    sent more than once."""

    statements: int
    connections: int
    most_held: int
    repeated: Mapping[str, Sequence[str]]


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
            found[route] = Reads(
                count.statements, count.connections, count.most_held, count.repeated()
            )
    return found


def grown(one: Mapping[str, Reads], page: Mapping[str, Reads]) -> dict[str, tuple[Reads, Reads]]:
    """Each route that made more statements or took more connections in `page` than in `one`,
    with both."""
    return {
        route: (one[route], read)
        for route, read in page.items()
        if read.statements > one[route].statements or read.connections > one[route].connections
    }


def held_at_once(*reads: Mapping[str, Reads]) -> dict[str, int]:
    """Each route that held more than one connection at once in any of `reads`, with the most."""
    most: dict[str, int] = {}
    for found in reads:
        for route, read in found.items():
            if read.most_held > 1:
                most[route] = max(most.get(route, 0), read.most_held)
    return most


def repeated(*reads: Mapping[str, Reads]) -> dict[str, Mapping[str, Sequence[str]]]:
    """Each route that sent a statement more than once in any of `reads`, with each statement
    and the code that sent it."""
    found: dict[str, Mapping[str, Sequence[str]]] = {}
    for each in reads:
        for route, read in each.items():
            if read.repeated:
                found[route] = {**found.get(route, {}), **read.repeated}
    return found


def tenants(
    where: Provisioned,
) -> tuple[tuple[str, dict[str, str]], tuple[str, dict[str, str]]]:
    """A tenant of one row and a tenant past a page, each with the path of each GET route."""
    one, page = f"one-{uuid4().hex}", f"page-{uuid4().hex}"
    one_paths, one_held = asyncio.run(fill(where, one, 1))
    page_paths, page_held = asyncio.run(fill(where, page, ROWS))
    short = {
        table: rows for table, rows in short_of(page_held).items() if table not in NOT_FILLED_YET
    }
    assert short == {}, (
        f"filled with {ROWS} rows left {short} rows in these tables, one page or less. Write as "
        f"many rows as filled is asked for, or name the table in NOT_FILLED_YET."
    )
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
    async with database.reading(tenant_id) as conn:
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


async def reads_the_first_page(request: Request, principal: PrincipalDep) -> int:
    database, tenant_id = tenant_of(request, principal)
    return len((await first_page(database, tenant_id)).tasks)


async def reads_the_first_page_twice(request: Request, principal: PrincipalDep) -> int:
    database, tenant_id = tenant_of(request, principal)
    first = await first_page(database, tenant_id)
    again = await first_page(database, tenant_id)
    assert again == first
    return len(first.tasks)


async def writes_while_reading(request: Request, principal: PrincipalDep) -> int:
    database, tenant_id = tenant_of(request, principal)
    page = await first_page(database, tenant_id)
    async with database.scoped(tenant_id) as conn:
        _ = await conn.execute("UPDATE tasks SET done = done WHERE id = $1", UUID(page.tasks[0].id))
    return len(page.tasks)


def test_no_get_route_reads_more_past_a_page_than_at_one_row(
    provisioned: Provisioned, monkeypatch: pytest.MonkeyPatch, count: Count
) -> None:
    (one, one_paths), (page, page_paths) = tenants(provisioned)
    app = create_app()
    routes = read_by_get(app) - set(NOT_READ_YET)
    for named, listed in (
        ("GROWS_WITH_ROWS", GROWS_WITH_ROWS),
        ("MORE_THAN_ONE_CONNECTION", MORE_THAN_ONE_CONNECTION),
        ("REPEATS_A_STATEMENT", REPEATS_A_STATEMENT),
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
    sent_again, sent_again_in_vain = against_the_list(
        repeated(at_one, past_a_page), REPEATS_A_STATEMENT
    )

    assert grew == {}, (
        f"Each of these GET routes made more statements or took more connections at {ROWS} rows "
        f"than at one, as (at one, at {ROWS}): {grew}. Read each fact of the items of a page in "
        f"one set read, through PostgresDatabase.loader in the store, or name the route in "
        f"GROWS_WITH_ROWS in tests/integration/filled.py, with its reason."
    )
    assert held == {}, (
        f"Each of these GET routes held more than one connection at once: {held}. Read through "
        f"PostgresDatabase.loader, or name the route in MORE_THAN_ONE_CONNECTION in "
        f"tests/integration/filled.py, with its reason."
    )
    assert sent_again == {}, (
        f"Each of these GET routes sent a statement to the database more than once in one "
        f"request, as {{route: {{statement: [the code that sent it, each time]}}}}: {sent_again}. "
        f"Read the same thing once, read each item's fact in one set read through "
        f"PostgresDatabase.loader, or name the route in REPEATS_A_STATEMENT in "
        f"tests/integration/filled.py, with its reason."
    )
    assert grew_in_vain == [], (
        f"GROWS_WITH_ROWS in tests/integration/filled.py names {grew_in_vain}. Each read no more "
        f"at {ROWS} rows than at one. Remove each from the list."
    )
    assert held_in_vain == [], (
        f"MORE_THAN_ONE_CONNECTION in tests/integration/filled.py names {held_in_vain}. Each held "
        f"one connection at a time. Remove each from the list."
    )
    assert sent_again_in_vain == [], (
        f"REPEATS_A_STATEMENT in tests/integration/filled.py names {sent_again_in_vain}. Each "
        f"sent each statement once. Remove each from the list."
    )


@pytest.mark.parametrize(
    ("named", "listed"),
    [
        ("GROWS_WITH_ROWS", GROWS_WITH_ROWS),
        ("MORE_THAN_ONE_CONNECTION", MORE_THAN_ONE_CONNECTION),
        ("REPEATS_A_STATEMENT", REPEATS_A_STATEMENT),
    ],
)
def test_each_entry_of_a_list_names_its_reason(named: str, listed: Mapping[str, str]) -> None:
    unreasoned = [entry for entry, reason in listed.items() if reason.strip() == ""]
    assert unreasoned == [], (
        f"{named} in tests/integration/filled.py gives {unreasoned} an empty reason. Write the "
        f"reason."
    )


def test_a_read_for_each_item_is_found_and_a_loader_reads_each_set_read_once(
    provisioned: Provisioned, monkeypatch: pytest.MonkeyPatch, count: Count
) -> None:
    (one, _), (page, _) = tenants(provisioned)
    app = create_app()
    planted = {EACH_ALONE: each_task_alone, EACH_AT_ONCE: each_task_at_once}
    routes = {
        **planted,
        EACH_LOADED: each_task_loaded,
        READS_ONCE: reads_the_first_page,
        READS_TWICE: reads_the_first_page_twice,
        WRITES_WHILE_READING: writes_while_reading,
    }
    for path, route in routes.items():
        app.add_api_route(path, route, methods=["GET"])
    paths = {path: path for path in routes}

    at_one = reads_of(provisioned, one, monkeypatch, app, paths, count)
    past_a_page = reads_of(provisioned, page, monkeypatch, app, paths, count)

    assert sorted(grown(at_one, past_a_page)) == sorted(planted)
    assert sorted(repeated(at_one, past_a_page)) == sorted(planted)
    assert list(held_at_once(at_one, past_a_page)) == [WRITES_WHILE_READING]
    assert past_a_page[EACH_LOADED] == at_one[EACH_LOADED]
    assert past_a_page[EACH_LOADED].connections == past_a_page[READS_ONCE].connections
    assert past_a_page[READS_TWICE] == past_a_page[READS_ONCE]
    assert past_a_page[EACH_ALONE].statements - at_one[EACH_ALONE].statements >= PAGE_MAX - 1


@dataclass(frozen=True)
class Overlap:
    """When a route reads, and when shared work that the lifespan started holds a connection."""

    route_started: asyncio.Event
    lifespan_holds: asyncio.Event
    route_done: asyncio.Event


async def holds(database: PostgresDatabase, holding: asyncio.Event, until: asyncio.Event) -> None:
    async with database.connection() as conn:
        _ = await conn.fetchval("SELECT 1")
        holding.set()
        await until.wait()


def with_lifespan_work_that_reads(app: FastAPI, overlap: Overlap) -> None:
    """Start a task in the lifespan of `app`. When a route starts, the task holds a connection in
    shared work until the route is done."""
    serves = app.router.lifespan_context
    shared = SharedWork[str, None]("held by the lifespan")

    async def reads(database: Any) -> None:
        assert isinstance(database, PostgresDatabase)
        await overlap.route_started.wait()
        await shared.joined(
            "held", lambda: holds(database, overlap.lifespan_holds, overlap.route_done)
        )

    @asynccontextmanager
    async def lifespan(started: FastAPI) -> AsyncGenerator[Any]:
        async with serves(started) as state, asyncio.TaskGroup() as group:
            work = group.create_task(reads(started.state.database))
            yield state
            _ = work.cancel()

    app.router.lifespan_context = lifespan


def while_others_hold(overlap: Overlap) -> Callable[..., Awaitable[int]]:
    """A route that reads while the lifespan's shared work holds a connection, and while shared
    work that its own work beside the request starts holds another."""
    shared = SharedWork[str, None]("held beside the request")

    async def route(request: Request, principal: PrincipalDep) -> int:
        database, tenant_id = tenant_of(request, principal)
        overlap.route_started.set()
        beside_holds, route_done = asyncio.Event(), asyncio.Event()
        held = beside(
            shared.joined("held", lambda: holds(database, beside_holds, route_done)),
            name="holds beside the request",
            within=5,
        )
        async with asyncio.timeout(5):
            _ = await asyncio.gather(overlap.lifespan_holds.wait(), beside_holds.wait())
        async with database.reading(tenant_id) as conn:
            tasks: int = await conn.fetchval("SELECT count(*) FROM tasks")
        route_done.set()
        overlap.route_done.set()
        await held
        return tasks

    return route


def test_work_that_serves_no_request_is_not_counted_as_the_request_s(
    provisioned: Provisioned, monkeypatch: pytest.MonkeyPatch, count: Count
) -> None:
    (one, _), _ = tenants(provisioned)
    overlap = Overlap(asyncio.Event(), asyncio.Event(), asyncio.Event())
    app = create_app()
    app.add_api_route(WHILE_OTHERS_HOLD, while_others_hold(overlap), methods=["GET"])
    with_lifespan_work_that_reads(app, overlap)
    read: list[Reads] = []
    with served(provisioned, one, monkeypatch, app) as client:
        for _ in range(2):
            count.clear()
            assert client.get(WHILE_OTHERS_HOLD).status_code == 200
            read.append(
                Reads(count.statements, count.connections, count.most_held, count.repeated())
            )

    assert overlap.lifespan_holds.is_set()
    assert read[0] == read[1]
    assert read[0].most_held == 1
