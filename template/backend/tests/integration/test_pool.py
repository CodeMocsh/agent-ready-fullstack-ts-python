"""The pool: every wait on Postgres has a bound that raises, and its connections can be
counted."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.store import Connections
from app.store.pg import AcquireTimedOut, PostgresDatabase, Timeouts
from tests.integration.conftest import Provisioned

SHORT = Timeouts(statement=0.2, idle_in_transaction=0.2, acquire=0.2)


@pytest.fixture
async def one_connection(provisioned: Provisioned) -> AsyncIterator[PostgresDatabase]:
    database = PostgresDatabase(
        dsn=provisioned.app_dsn, schema=provisioned.schema, min_size=1, max_size=1, timeouts=SHORT
    )
    await database.check()
    try:
        yield database
    finally:
        await database.close()


def driver() -> Any:
    import asyncpg

    return asyncpg


async def test_a_statement_that_runs_too_long_is_cancelled_by_postgres(
    one_connection: PostgresDatabase,
) -> None:
    async with one_connection.connection() as conn:
        with pytest.raises(driver().QueryCanceledError, match="statement timeout"):
            await conn.execute("SELECT pg_sleep(5)")


async def test_a_transaction_left_idle_is_ended_by_postgres_and_its_connection_replaced(
    one_connection: PostgresDatabase,
) -> None:
    async with one_connection.connection() as conn:
        await conn.transaction().start()
        await asyncio.sleep(1)
        with pytest.raises(driver().InterfaceError):
            await conn.execute("SELECT 1")

    assert await one_connection.store("a-tenant").list() == []


async def test_a_request_that_finds_every_connection_in_use_waits_then_raises(
    one_connection: PostgresDatabase,
) -> None:
    async with one_connection.connection():
        with pytest.raises(AcquireTimedOut, match="all 1 stayed in use"):
            await one_connection.store("a-tenant").list()


async def test_a_connection_lent_out_is_counted_as_used_until_it_comes_back(
    provisioned: Provisioned, one_connection: PostgresDatabase
) -> None:
    async with one_connection.connection():
        lent = one_connection.connections()
    returned = one_connection.connections()

    assert lent == Connections(pool=provisioned.schema, used=1, idle=0, max_size=1)
    assert returned == Connections(pool=provisioned.schema, used=0, idle=1, max_size=1)


async def test_a_closed_pool_counts_no_connections(
    provisioned: Provisioned, one_connection: PostgresDatabase
) -> None:
    await one_connection.close()

    assert one_connection.connections() == Connections(
        pool=provisioned.schema, used=0, idle=0, max_size=1
    )
