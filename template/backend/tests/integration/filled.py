"""How this project fills a tenant, where each GET route reads it, and who reads, for
`tests/integration/test_routes_read_pages.py` and
`tests/integration/test_routes_read_a_fixed_count.py`. The project owns this module: a new project
gets it once, and an update never writes it. `docs/adr/template/0015`, `0026` and `0032`.

`filled` writes `rows` rows of the tenant to each table in `TENANT_TABLES`, and answers the path
that the tests read for each GET route in `openapi.json`: a route template as the key, and a path
that answers `200` as the value. The tests fill one tenant with `ROWS` and one with a single row.
A table or a route that it does not cover fails the test, and the failure names it. `reader`
answers the principal of each read. `OVER_A_PAGE` names each GET route whose read may fetch more
than a page until it reads a page. `GROWS_WITH_ROWS` names each GET route whose reads may grow
with its rows, and `MORE_THAN_ONE_CONNECTION` each that may hold more than one connection at once.
`NOT_FILLED_YET` names each tenant table that `filled` does not fill past a page yet, and
`NOT_READ_YET` each GET route that it answers no path for yet. Each list only shrinks.
"""

from app.audit_events import acting_as
from app.identity.shared import Actor, Principal
from app.models import PAGE_MAX, CreateTaskBody
from app.store import Database

FILLS: Actor = Actor(issuer="a-test", subject="the-filler", name="The filler")
"""Who writes the rows. A tenant table takes a write only from an actor."""

ROWS = PAGE_MAX + 2
"""How many rows each table gets in the tenant past a page: one past what a page read fetches."""

READS: Actor = Actor(issuer="a-test", subject="the-reader", name="The reader")
"""Who reads each GET route."""

OVER_A_PAGE: dict[str, str] = {}
"""Each GET route template whose read fetches more than `PAGE_MAX + 1` rows, and the reason. The
test fails on a route that the list names and that reads a page, and on a reason that is empty.
Remove a route when it reads a page."""

GROWS_WITH_ROWS: dict[str, str] = {}
"""Each GET route template whose statements or connections at `ROWS` rows are more than at one
row, and the reason. The test fails on a route that the list names and that reads as much at both,
and on a reason that is empty. Remove a route when its reads do not grow with its rows."""

MORE_THAN_ONE_CONNECTION: dict[str, str] = {}
"""Each GET route template that holds more than one connection at once, and the reason. The test
fails on a route that the list names and that holds one at a time, and on a reason that is empty.
Remove a route when it holds one connection at a time."""

NOT_FILLED_YET: dict[str, str] = {}
"""Each tenant table that `filled` leaves at one page or less, and the reason. The test fails on a
table that the list names and that `filled` fills past a page, and on a reason that is empty.
Remove a table when `filled` fills it."""

NOT_READ_YET: dict[str, str] = {}
"""Each GET route template that `filled` answers no path for, and the reason. The test reads no
such route. It fails on a route that the list names and that `filled` answers a path for, and on
a reason that is empty. Remove a route when `filled` answers its path."""

A_KIND = "answers who asked"
"""A kind of job in `tests.jobs_contract.contract_kinds`. Nothing runs it here."""


async def filled(database: Database, tenant_id: str, rows: int) -> dict[str, str]:
    """Write `rows` rows of `tenant_id` to each tenant table, and answer the path of each GET
    route."""
    with acting_as(FILLS):
        store = database.store(tenant_id)
        for n in range(rows):
            _ = await store.create(CreateTaskBody(title=f"task {n}"))
        jobs = database.jobs(tenant_id)
        asked = [await jobs.ask(A_KIND, {"n": n}) for n in range(rows)]
    return {"/tasks": "/tasks", "/jobs/{id}": f"/jobs/{asked[0].id}"}


def reader(tenant_id: str) -> Principal:
    """The principal that reads each GET route in `tenant_id`."""
    return Principal(tenant_id=tenant_id, actor=READS)
