"""Every GET route reads a page. On a tenant that holds more than a page in each table, no read in
a request fetches more than `PAGE_MAX + 1` rows. `docs/adr/template/0026`.

The template owns this module. `tests/integration/filled.py` is the project's list: how it fills a
tenant, the path of each GET route in `openapi.json`, who reads, each route that may fetch more
than a page until it reads a page, and each table and route that it does not fill or read yet.
"""

import asyncio
from collections.abc import Mapping
from typing import Any, Final
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.deps import PrincipalDep, database_of
from app.main import create_app
from app.models import PAGE_MAX
from app.store.pg import PostgresDatabase
from tests.integration.conftest import Provisioned
from tests.integration.filled import NOT_FILLED_YET, NOT_READ_YET, OVER_A_PAGE, ROWS
from tests.integration.reading import (
    Count,
    against_the_list,
    counting,
    fill,
    read_by_get,
    served,
    short_of,
)

PLANTED: Final = "/every-task"


@pytest.fixture
def count(monkeypatch: pytest.MonkeyPatch) -> Count:
    return counting(monkeypatch)


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


def over_a_page(client: TestClient, paths: dict[str, str], count: Count) -> dict[str, list[int]]:
    """Each route whose read fetched more than `PAGE_MAX + 1` rows, with the rows of each read.
    Fails on a path that does not answer `200`."""
    found: dict[str, list[int]] = {}
    for route, path in sorted(paths.items()):
        count.clear()
        answered = client.get(path)
        assert answered.status_code == 200, (
            f"{path} answered {answered.status_code} for {route}: {answered.text}"
        )
        if max(count.fetched, default=0) > PAGE_MAX + 1:
            found[route] = list(count.fetched)
    return found


async def reads_every_task(request: Request, principal: PrincipalDep) -> int:
    database = database_of(request)
    assert isinstance(database, PostgresDatabase)
    async with database.reading(principal.tenant_id) as conn:
        rows: list[Any] = await conn.fetch("SELECT id FROM tasks")
    return len(rows)


def test_each_get_route_fetches_at_most_a_page_for_each_read(
    provisioned: Provisioned, monkeypatch: pytest.MonkeyPatch, count: Count
) -> None:
    tenant_id = f"filled-{uuid4().hex}"
    paths, held = asyncio.run(fill(provisioned, tenant_id, ROWS))
    app = create_app()
    every_get = read_by_get(app)
    found = gaps(short_of(held), paths, every_get, NOT_FILLED_YET, NOT_READ_YET)
    assert found == {}, f"tests/integration/filled.py disagrees with the app: {found}"
    routes = every_get - set(NOT_READ_YET)
    assert sorted(set(OVER_A_PAGE) - routes) == [], (
        f"OVER_A_PAGE in tests/integration/filled.py names "
        f"{sorted(set(OVER_A_PAGE) - routes)}, which is no GET route that this test reads"
    )

    with served(provisioned, tenant_id, monkeypatch, app) as client:
        unlisted, listed_in_vain = against_the_list(over_a_page(client, paths, count), OVER_A_PAGE)

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
    provisioned: Provisioned, monkeypatch: pytest.MonkeyPatch, count: Count
) -> None:
    tenant_id = f"filled-{uuid4().hex}"
    _ = asyncio.run(fill(provisioned, tenant_id, ROWS))
    app = create_app()

    app.add_api_route(PLANTED, reads_every_task, methods=["GET"])

    with served(provisioned, tenant_id, monkeypatch, app) as client:
        found = over_a_page(client, {PLANTED: PLANTED}, count)

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
