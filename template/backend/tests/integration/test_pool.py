"""The pool: it is sized from the connection budget, every wait on Postgres has a bound that
raises, a connection the server refuses or ends is a refusal of one unit of work, and its
connections can be counted."""

import asyncio
import math
import time
from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import uuid4

import pytest

from app.audit_events import acting_as
from app.deployment import DATABASE_URL_ENV, IDLE_IN_TRANSACTION_TIMEOUT_ENV, STATEMENT_TIMEOUT_ENV
from app.identity.shared import SENTINEL_TENANT
from app.jobs import kinds
from app.store import Connections
from app.store.budget import Draws, OverBudget
from app.store.conn import SCHEMA_ENV
from app.store.migrate import known_version
from app.store.pg import (
    TIMEOUTS,
    AcquireTimedOut,
    ConnectionLost,
    ConnectionRefused,
    PostgresDatabase,
    Timeouts,
)
from app.wiring import build
from tests.clock import elapse
from tests.event_loop import Reported, hold_the_event_loop
from tests.integration.conftest import THE_SUITE, Provisioned, connect, end_session
from tests.pages import every_task

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
            draws=Draws(instances=1, in_process={}, outside={}, pool=1),
            kinds=kinds,
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

    with pytest.raises(ConnectionLost, match="InterfaceError"):
        async with database.connection() as conn:
            await conn.transaction().start()
            await elapse(1)
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
            await elapse(0.05)
            await conn.execute("SELECT 1")

    async def busy() -> None:
        await asyncio.wait_for(begun.wait(), timeout=5)
        hold_the_event_loop(1)

    ended, _ = await asyncio.gather(held(), busy(), return_exceptions=True)

    assert isinstance(ended, ConnectionLost)
    assert await database.check() == known_version()
    assert asyncio_reports.held_on_purpose() != []


async def test_a_request_that_finds_every_connection_in_use_waits_then_raises(
    one_connection_under: Bounded,
) -> None:
    database = one_connection_under(Timeouts(acquire=SHORT))
    timed: list[float] = []
    database.time_waits(timed.append)
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
    [timed_out] = timed
    assert SHORT / 2 <= timed_out <= waited


async def test_the_pool_times_each_wait_that_ends_in_a_connection(
    one_connection: PostgresDatabase,
) -> None:
    timed: list[float] = []
    one_connection.time_waits(timed.append)

    async with one_connection.connection():
        pass
    async with one_connection.connection():
        pass

    assert len(timed) == 2
    assert all(0 <= seconds < TIMEOUTS.acquire for seconds in timed)


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


async def server_allows(provisioned: Provisioned) -> tuple[int, int]:
    conn = await connect(provisioned.admin_dsn)
    try:
        rows = await conn.fetch(
            "SELECT name, setting::int FROM pg_settings WHERE name IN "
            "('max_connections', 'superuser_reserved_connections', 'reserved_connections')"
        )
    finally:
        await conn.close()
    settings = {row["name"]: row["setting"] for row in rows}
    return settings.pop("max_connections"), sum(settings.values())


async def test_the_pool_takes_what_remains_of_the_process_s_share_of_the_server(
    provisioned: Provisioned,
) -> None:
    most, reserved = await server_allows(provisioned)
    database = PostgresDatabase(
        dsn=provisioned.app_dsn,
        schema=provisioned.schema,
        draws=Draws(instances=2, in_process={"a feed": 3}, outside={"the release step": 1}),
        kinds=kinds,
    )
    try:
        pool = await database.pool()
        budget = database.budget()
    finally:
        await database.close()

    assert budget.pool == (most - reserved - 1) // 2 - 3
    assert pool.get_max_size() == budget.pool


async def test_draws_the_server_cannot_hold_refuse_to_open(provisioned: Provisioned) -> None:
    most, _ = await server_allows(provisioned)
    database = PostgresDatabase(
        dsn=provisioned.app_dsn,
        schema=provisioned.schema,
        draws=Draws(instances=most, in_process={}, outside={}),
        kinds=kinds,
    )

    with pytest.raises(OverBudget, match=f"each of {most} processes"):
        await database.check()


async def test_a_connection_the_server_refuses_is_a_refusal_of_one_request(
    provisioned: Provisioned,
) -> None:
    admin = await connect(provisioned.admin_dsn)
    try:
        await admin.execute(f'ALTER ROLE "{provisioned.app}" CONNECTION LIMIT 1')
    finally:
        await admin.close()
    database = PostgresDatabase(
        dsn=provisioned.app_dsn,
        schema=provisioned.schema,
        draws=Draws(instances=1, in_process={}, outside={}, pool=2),
        kinds=kinds,
    )
    timed: list[float] = []
    try:
        async with database.connection():
            database.time_waits(timed.append)
            with pytest.raises(ConnectionRefused, match="TooManyConnectionsError") as refused:
                await database.check()
            assert timed == []
        assert await database.check() == known_version()
    finally:
        await database.close()

    assert refused.value.retry_after == math.ceil(TIMEOUTS.acquire)
    assert len(timed) == 1


async def _ended_after(admin_dsn: str, pid: int) -> None:
    await elapse(SHORT)
    await end_session(admin_dsn, pid)


KILLS = 3
"""More kills than the pool of one has slots, so a slot that one kill keeps fails the next."""


async def test_a_connection_ended_during_a_statement_is_lost_and_gives_its_slot_back(
    provisioned: Provisioned, one_connection: PostgresDatabase
) -> None:
    for _ in range(KILLS):
        with pytest.raises(ConnectionLost) as lost:
            async with one_connection.connection() as conn:
                pid = await conn.fetchval("SELECT pg_backend_pid()")
                ending = asyncio.get_running_loop().create_task(
                    _ended_after(provisioned.admin_dsn, pid)
                )
                try:
                    await conn.execute("SELECT pg_sleep(3)")
                finally:
                    await ending

        assert lost.value.retry_after == math.ceil(TIMEOUTS.acquire)

    assert await one_connection.check() == known_version()


async def test_a_transaction_whose_connection_ends_between_statements_writes_nothing(
    provisioned: Provisioned, one_connection: PostgresDatabase
) -> None:
    for _ in range(KILLS):
        with acting_as(THE_SUITE), pytest.raises(ConnectionLost):
            async with one_connection.scoped(SENTINEL_TENANT) as conn:
                await conn.execute(
                    "INSERT INTO tasks (id, tenant_id, title) VALUES ($1, $2, 'lost')",
                    uuid4(),
                    SENTINEL_TENANT,
                )
                await end_session(
                    provisioned.admin_dsn, await conn.fetchval("SELECT pg_backend_pid()")
                )
                await elapse(SHORT)
                await conn.execute("SELECT 1")

    assert await every_task(one_connection.store(SENTINEL_TENANT)) == []
