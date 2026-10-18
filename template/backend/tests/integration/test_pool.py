"""The pool: every wait on Postgres has a bound that raises, and its connections can be
counted."""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest

from app.deployment import DATABASE_URL_ENV, IDLE_IN_TRANSACTION_TIMEOUT_ENV, STATEMENT_TIMEOUT_ENV
from app.store import Connections
from app.store.conn import SCHEMA_ENV
from app.store.migrate import known_version
from app.store.pg import TIMEOUTS, AcquireTimedOut, PostgresDatabase, Timeouts
from app.wiring import build
from tests.event_loop import Reported
from tests.integration.conftest import Provisioned

SHORT = 0.2

Bounded = Callable[[Timeouts], PostgresDatabase]


@pytest.fixture
async def one_connection_under(provisioned: Provisioned) -> AsyncIterator[Bounded]:
    """Builds a pool of one connection under the bounds a test passes, and closes every pool it
    built. A test passes a short bound only for the wait it tests."""
    opened: list[PostgresDatabase] = []

    def bounded(timeouts: Timeouts) -> PostgresDatabase:
        database = PostgresDatabase(
            dsn=provisioned.app_dsn,
            schema=provisioned.schema,
            min_size=1,
            max_size=1,
            timeouts=timeouts,
        )
        opened.append(database)
        return database

    try:
        yield bounded
    finally:
        await asyncio.gather(*(asyncio.wait_for(one.close(), timeout=5) for one in opened))


@pytest.fixture
def one_connection(one_connection_under: Bounded) -> PostgresDatabase:
    return one_connection_under(TIMEOUTS)


def driver() -> Any:
    import asyncpg

    return asyncpg


async def test_a_statement_that_runs_too_long_is_cancelled_by_postgres(
    one_connection_under: Bounded,
) -> None:
    database = one_connection_under(Timeouts(statement=SHORT))

    async with database.connection() as conn:
        with pytest.raises(driver().QueryCanceledError, match="statement timeout"):
            await conn.execute("SELECT pg_sleep(3)")
        conn.terminate()


async def test_a_transaction_left_idle_is_ended_by_postgres_and_its_connection_replaced(
    one_connection_under: Bounded,
) -> None:
    database = one_connection_under(Timeouts(idle_in_transaction=SHORT))

    async with database.connection() as conn:
        await conn.transaction().start()
        await asyncio.sleep(1)
        with pytest.raises(driver().InterfaceError):
            await conn.execute("SELECT 1")

    assert await database.check() == known_version()


async def test_a_transaction_ended_by_postgres_while_the_loop_was_busy_gives_its_slot_back(
    one_connection_under: Bounded, asyncio_reports: Reported
) -> None:
    database = one_connection_under(Timeouts(idle_in_transaction=SHORT))
    begun = asyncio.Event()

    async def held() -> None:
        async with database.connection() as conn, conn.transaction():
            await conn.execute("SELECT 1")
            begun.set()
            await asyncio.sleep(0.05)
            await conn.execute("SELECT 1")

    async def busy() -> None:
        await asyncio.wait_for(begun.wait(), timeout=5)
        time.sleep(1)

    ended, _ = await asyncio.gather(held(), busy(), return_exceptions=True)

    assert isinstance(ended, driver().InterfaceError)
    assert await database.check() == known_version()
    assert asyncio_reports.held_on_purpose() != []


async def test_a_request_that_finds_every_connection_in_use_waits_then_raises(
    one_connection_under: Bounded,
) -> None:
    database = one_connection_under(Timeouts(acquire=SHORT))
    pool = await database.pool()
    held = await pool.acquire(timeout=TIMEOUTS.acquire)

    try:
        started = time.monotonic()
        with pytest.raises(AcquireTimedOut, match="all 1 stayed in use"):
            await asyncio.wait_for(database.check(), timeout=TIMEOUTS.acquire / 2)
        waited = time.monotonic() - started
    finally:
        await pool.release(held)

    assert waited >= SHORT / 2


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
    await one_connection.check()
    await one_connection.close()

    assert one_connection.connections() == Connections(
        pool=provisioned.schema, used=0, idle=0, max_size=1
    )


async def test_the_bounds_a_deployment_sets_are_the_ones_postgres_holds(
    provisioned: Provisioned, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DATABASE_URL_ENV, provisioned.app_dsn)
    monkeypatch.setenv(SCHEMA_ENV, provisioned.schema)
    monkeypatch.setenv(STATEMENT_TIMEOUT_ENV, "2.5")
    monkeypatch.setenv(IDLE_IN_TRANSACTION_TIMEOUT_ENV, "7.5")
    database = build()
    assert isinstance(database, PostgresDatabase)
    await database.check()
    try:
        async with database.connection() as conn:
            statement = await conn.fetchval("SHOW statement_timeout")
            idle = await conn.fetchval("SHOW idle_in_transaction_session_timeout")
    finally:
        await database.close()

    assert (statement, idle) == ("2500ms", "7500ms")
