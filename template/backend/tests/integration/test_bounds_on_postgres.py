"""Every column of variable width holds a bounded number of bytes, and Postgres holds every writer
to it: the application, a worker, a script. `docs/adr/template/0026`."""

import re
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any, Final

import asyncpg
import pytest

from app.audit_events import acting_as
from app.models import PAGE_BYTES, CreateTaskBody
from app.store import Database
from app.store.ddl import BOUND_SUFFIX, bound_name
from tests.integration.conftest import (
    Provisioned,
    acting_on,
    apply_schema,
    connect,
    postgres_dsn,
)
from tests.store_contract import ACTOR

UNBOUNDED_COLUMNS: Final[dict[str, str]] = {}
"""Each column that holds values over the bound it should have, as `table.column`, and why it
is not bounded yet. A column leaves this list in the change that moves its values out of the row
and bounds it. A column on the list that has a bound fails, so the list only shrinks."""

_VARIABLE_WIDTH: Final = """
SELECT c.relname AS table_name, a.attname AS column_name
  FROM pg_attribute AS a JOIN pg_class AS c ON c.oid = a.attrelid
 WHERE c.relnamespace = $1::regnamespace AND c.relkind = 'r'
   AND a.attnum > 0 AND NOT a.attisdropped AND a.attlen = -1
 ORDER BY 1, 2
"""

_BOUNDS: Final = """
SELECT conrelid::regclass::text AS table_name, conname, convalidated,
       pg_get_constraintdef(oid) AS rule
  FROM pg_constraint
 WHERE connamespace = $1::regnamespace AND contype = 'c' AND conname LIKE '%' || $2
"""

_MOST: Final = re.compile(r"octet_length\(.*\) <= (\d+)\)\)")


async def catalog(where: Provisioned, query: str, *args: Any) -> list[Any]:
    conn = await connect(where.admin_dsn)
    try:
        return await conn.fetch(query, where.schema, *args)
    finally:
        await conn.close()


async def bounds(where: Provisioned) -> dict[str, tuple[bool, int]]:
    """Each bound in the schema, by name: whether it is valid, and the bytes it allows."""
    found: dict[str, tuple[bool, int]] = {}
    for row in await catalog(where, _BOUNDS, BOUND_SUFFIX):
        most = _MOST.search(row["rule"])
        assert most is not None, f"{row['conname']} bounds no bytes: {row['rule']}"
        found[row["conname"]] = (row["convalidated"], int(most.group(1)))
    return found


@pytest.fixture(autouse=True)
async def acting() -> AsyncIterator[None]:
    with acting_as(ACTOR):
        yield


async def test_every_column_of_variable_width_has_a_valid_bound(provisioned: Provisioned) -> None:
    found = await bounds(provisioned)
    columns = [
        f"{row['table_name']}.{row['column_name']}"
        for row in await catalog(provisioned, _VARIABLE_WIDTH)
    ]
    named = {column: bound_name(*column.split(".")) for column in columns}

    unbounded = [
        column
        for column in columns
        if column not in UNBOUNDED_COLUMNS and not found.get(named[column], (False, 0))[0]
    ]
    stale = [column for column in UNBOUNDED_COLUMNS if named.get(column) in found]

    assert unbounded == [], (
        f"{unbounded} hold any number of bytes. Add a bound for each to COLUMN_BYTES in "
        f"app/store/ddl.py, or move its values out of the row."
    )
    assert stale == [], f"{stale} are bounded. Remove them from UNBOUNDED_COLUMNS."


async def test_the_bounds_of_one_row_fit_one_page(provisioned: Provisioned) -> None:
    found = await bounds(provisioned)
    rows: dict[str, int] = defaultdict(int)
    for row in await catalog(provisioned, _BOUNDS, BOUND_SUFFIX):
        rows[row["table_name"]] += found[row["conname"]][1]

    assert {table: most for table, most in rows.items() if most > PAGE_BYTES} == {}


async def test_postgres_refuses_a_write_over_its_bound(database: Database) -> None:
    store = database.store("bounded")

    with pytest.raises(asyncpg.CheckViolationError, match="tasks_title_bytes"):
        await store.create(CreateTaskBody.model_construct(title="x" * 2001))


async def test_a_stored_value_over_a_new_bound_stops_the_migration_and_names_it(
    provisioned: Provisioned,
) -> None:
    conn = await acting_on(await connect(provisioned.admin_dsn))
    try:
        await conn.execute(f'SET search_path TO "{provisioned.schema}"')
        await conn.execute("ALTER TABLE tasks DROP CONSTRAINT tasks_title_bytes")
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, title) VALUES (gen_random_uuid(), 'wide', $1)",
            "x" * 2001,
        )
    finally:
        await conn.close()

    with pytest.raises(asyncpg.RaiseError, match=r"tasks_title_bytes CHECK") as refused:
        await apply_schema(postgres_dsn(), provisioned.schema)

    assert "stored values are longer than their bound" in str(refused.value)
    assert (await bounds(provisioned)).get("tasks_title_bytes", (False, 0))[0] is False
