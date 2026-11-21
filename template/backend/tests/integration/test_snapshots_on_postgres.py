"""A snapshot on Postgres: one transaction, READ ONLY and REPEATABLE READ, that sees no write
committed after it opened, except one the request made through `scoped`.
`docs/adr/template/0033`."""

from typing import Any, Final

import asyncpg
import pytest

from app.audit_events import acting_as
from app.models import CreateTaskBody
from app.store import Database
from app.store.acting import SET_TENANT_AND_ACTING, acting
from app.store.snapshots import snapshotted
from tests.integration.conftest import THE_SUITE

TENANT: Final = "acme"

COUNT: Final = "SELECT count(*) FROM tasks"

COUNT_AGAIN: Final = "SELECT count(*) FROM tasks WHERE true"

INSERT: Final = "INSERT INTO tasks (id, tenant_id, title) VALUES (gen_random_uuid(), $1, $2)"


async def counted(database: Any, query: str = COUNT) -> int:
    async with database.reading(TENANT) as conn:
        tasks: int = await conn.fetchval(query)
    return tasks


async def created(database: Database, title: str) -> None:
    with acting_as(THE_SUITE):
        _ = await database.store(TENANT).create(CreateTaskBody(title=title))


async def created_elsewhere(database: Any) -> None:
    conn = await database.dedicated()
    try:
        async with conn.transaction():
            await conn.execute(SET_TENANT_AND_ACTING, TENANT, *acting(THE_SUITE, None, None))
            await conn.execute(INSERT, TENANT, "elsewhere")
    finally:
        await conn.close()


async def test_a_snapshot_sees_no_write_that_another_connection_commits_after_it_opened(
    database: Database,
) -> None:
    await created(database, "before")
    async with snapshotted() as scope:
        before = await counted(database)
        await created_elsewhere(database)
        again = await counted(database, COUNT_AGAIN)
        await scope.end()
        after = await counted(database)

    assert (before, again, after) == (1, 1, 2)


async def test_a_read_in_a_snapshot_sees_a_write_that_the_request_made_through_scoped(
    database: Database,
) -> None:
    async with snapshotted():
        before = await counted(database)
        await created(database, "in the request")
        after = await counted(database)

    assert (before, after) == (0, 1)


async def test_a_write_in_a_snapshot_is_refused(database: Any) -> None:
    async with snapshotted():
        with pytest.raises(asyncpg.exceptions.ReadOnlySQLTransactionError):
            async with database.reading(TENANT) as conn:
                await conn.execute(INSERT, TENANT, "refused")
        assert await counted(database) == 0


async def test_a_write_through_reading_outside_a_get_request_is_refused(database: Any) -> None:
    with pytest.raises(asyncpg.exceptions.ReadOnlySQLTransactionError):
        async with database.reading(TENANT) as conn:
            await conn.execute(INSERT, TENANT, "refused")


async def test_a_write_on_a_connection_of_its_own_is_seen_after_written(database: Any) -> None:
    async with snapshotted():
        before = await counted(database)
        with acting_as(THE_SUITE):
            async with database.connection() as conn, conn.transaction():
                await conn.execute(SET_TENANT_AND_ACTING, TENANT, *acting(THE_SUITE, None, None))
                await conn.execute(INSERT, TENANT, "on a connection of its own")
        unseen = await counted(database)
        await database.written(TENANT)
        seen = await counted(database)

    assert (before, unseen, seen) == (0, 0, 1)
