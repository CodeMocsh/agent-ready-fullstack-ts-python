"""The pool: every wait on Postgres has a bound that raises, and its connections can be
counted."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.store import Connections
from app.store.pg import PoolExhausted, PostgresDatabase, Timeouts
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
        with pytest.raises(PoolExhausted, match="all 1 were in use"):
            await one_connection.store("a-tenant").list()


async def test_a_connection_lent_out_is_counted_as_used_until_it_comes_back(
    one_connection: PostgresDatabase,
) -> None:
    async with one_connection.connection():
        assert one_connection.connections() == Connections(used=1, idle=0, limit=1)
    assert one_connection.connections() == Connections(used=0, idle=1, limit=1)


async def test_a_closed_pool_counts_no_connections(one_connection: PostgresDatabase) -> None:
    await one_connection.close()

    assert one_connection.connections() == Connections(used=0, idle=0, limit=1)
