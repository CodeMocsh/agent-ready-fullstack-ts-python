"""How this project fills a tenant past one page, where each GET route reads it, and who reads,
for `tests/integration/test_routes_read_pages.py`. The project owns this module: a new project
gets it once, and an update never writes it. `docs/adr/template/0015` and `0026`.

`filled` writes more than `PAGE_MAX + 1` rows of the tenant to each table in `TENANT_TABLES`, and
answers the path that the test reads for each GET route in `openapi.json`: a route template as
the key, and a path that answers `200` as the value. A table or a route that it does not cover
fails the test, and the failure names it. `reader` answers the principal of each read.
"""

from app.audit_events import acting_as
from app.identity.shared import Actor, Principal
from app.models import PAGE_MAX, CreateTaskBody
from app.store import Database

FILLS: Actor = Actor(issuer="a-test", subject="the-filler", name="The filler")
"""Who writes the rows. A tenant table takes a write only from an actor."""

ROWS = PAGE_MAX + 2
"""How many rows each table gets: one past what a page read fetches."""

READS: Actor = Actor(issuer="a-test", subject="the-reader", name="The reader")
"""Who reads each GET route."""

A_KIND = "answers who asked"
"""A kind of job in `tests.jobs_contract.contract_kinds`. Nothing runs it here."""


async def filled(database: Database, tenant_id: str) -> dict[str, str]:
    """Fill `tenant_id` past one page in each tenant table, and answer the path of each GET
    route."""
    with acting_as(FILLS):
        store = database.store(tenant_id)
        for n in range(ROWS):
            _ = await store.create(CreateTaskBody(title=f"task {n}"))
        jobs = database.jobs(tenant_id)
        asked = [await jobs.ask(A_KIND, {"n": n}) for n in range(ROWS)]
    return {"/tasks": "/tasks", "/jobs/{id}": f"/jobs/{asked[0].id}"}


def reader(tenant_id: str) -> Principal:
    """The principal that reads each GET route in `tenant_id`."""
    return Principal(tenant_id=tenant_id, actor=READS)
