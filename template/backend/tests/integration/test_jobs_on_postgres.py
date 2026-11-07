"""What only Postgres keeps of the jobs contract: the fence, the lease, the wake, and a second
process. `docs/adr/template/0027`.

The worker process itself, on the example's kind, is in `tests/integration/test_tasks_on_postgres.py`.
A worker dies here as a cancelled task: it releases nothing, as a killed process releases
nothing, so another worker gets the job only when the lease ends.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import Final

import pytest

from app.audit_events import acting_as
from app.identity.shared import SENTINEL_TENANT, Actor
from app.models import CreateTaskBody, Job, JsonObject
from app.store import TaskStore
from app.store.jobs import LEASE_LOST, MAX_ATTEMPTS, Handler, LeaseLost, Leases, fenced
from app.store.pg import PostgresDatabase
from app.store.pg_jobs import JobClaims
from app.worker import run, work
from tests.integration.conftest import ONE_PROCESS, Provisioned
from tests.jobs_contract import ASKER
from tests.pages import every_task

SHORT: Final = Leases(lease=0.5, renewal=0.1, retry=0.01)
"""A lease that ends half a second after nobody renews it."""

WITHIN: Final = 15.0
"""The longest a test waits for a job to end."""

STARTED: Final[dict[str, asyncio.Event]] = {}
"""Set when an attempt of the job with the key in its input starts."""


async def starts_and_waits(_store: TaskStore, input: JsonObject) -> JsonObject:
    STARTED.setdefault(str(input["key"]), asyncio.Event()).set()
    _ = await asyncio.Event().wait()
    return {}


async def ends_at_once(_store: TaskStore, _input: JsonObject) -> JsonObject:
    return {"ended": True}


async def creates_a_task(store: TaskStore, _input: JsonObject) -> JsonObject:
    return {"id": (await store.create(CreateTaskBody(title="from a job"))).id}


A_KIND: Final = "a kind"


def kinds(handler: Handler) -> dict[str, Handler]:
    return {A_KIND: handler}


@pytest.fixture(autouse=True)
def acting() -> Iterator[Actor]:
    with acting_as(ASKER) as actor:
        yield actor


@pytest.fixture
async def opened(provisioned: Provisioned) -> AsyncIterator[Callable[[Handler], PostgresDatabase]]:
    """Each call opens a database on the one schema, whose jobs of `A_KIND` the handler runs."""
    held: list[PostgresDatabase] = []

    def opening(handler: Handler) -> PostgresDatabase:
        database = PostgresDatabase(
            dsn=provisioned.app_dsn,
            schema=provisioned.schema,
            draws=ONE_PROCESS,
            kinds=kinds(handler),
        )
        held.append(database)
        return database

    try:
        yield opening
    finally:
        for database in held:
            await database.close()


async def ended(database: PostgresDatabase, id: str) -> Job:
    """The job `id` once it is not pending. Fails the test after `WITHIN`."""
    async with asyncio.timeout(WITHIN):
        while True:
            job = await database.jobs(SENTINEL_TENANT).read(id)
            assert job is not None
            if job.state != "pending":
                return job
            await asyncio.sleep(0.05)


async def until(condition: Callable[[], Awaitable[bool]]) -> None:
    async with asyncio.timeout(WITHIN):
        while not await condition():
            await asyncio.sleep(0.02)


async def test_an_attempt_whose_lease_ended_writes_nothing_after_another_worker_claims_the_job(
    opened: Callable[[Handler], PostgresDatabase],
) -> None:
    database = opened(creates_a_task)
    claims = JobClaims(database, SHORT)
    _ = await database.jobs(SENTINEL_TENANT).ask(A_KIND, {})
    slow = await claims.claim([A_KIND])
    assert slow is not None
    await asyncio.sleep(SHORT.lease + 0.2)

    fast = await claims.claim([A_KIND])

    assert fast is not None
    assert (fast.id, fast.attempts) == (slow.id, 2)
    with fenced(slow), pytest.raises(LeaseLost):
        _ = await database.store(SENTINEL_TENANT).create(CreateTaskBody(title="too late"))
    assert await every_task(database.store(SENTINEL_TENANT)) == []


async def test_a_job_whose_transaction_is_open_is_not_claimed_when_its_lease_ends(
    opened: Callable[[Handler], PostgresDatabase],
) -> None:
    database = opened(creates_a_task)
    claims = JobClaims(database, SHORT)
    _ = await database.jobs(SENTINEL_TENANT).ask(A_KIND, {})
    held = await claims.claim([A_KIND])
    assert held is not None

    with fenced(held):
        async with database.scoped(SENTINEL_TENANT):
            await asyncio.sleep(SHORT.lease + 0.2)
            assert await claims.claim([A_KIND]) is None

    after = await claims.claim([A_KIND])
    assert after is not None
    assert after.id == held.id


async def test_a_job_whose_worker_died_is_ended_by_another_worker(
    opened: Callable[[Handler], PostgresDatabase],
) -> None:
    key = "died"
    dies = opened(starts_and_waits)
    asked = await dies.jobs(SENTINEL_TENANT).ask(A_KIND, {"key": key})
    dying = asyncio.get_running_loop().create_task(
        work(JobClaims(dies, SHORT), kinds(starts_and_waits), at_once=1)
    )
    await until(lambda: _started(key))
    _ = dying.cancel()
    _ = await asyncio.wait([dying])

    lives = opened(ends_at_once)
    living = asyncio.get_running_loop().create_task(
        work(JobClaims(lives, SHORT), kinds(ends_at_once), at_once=1, sweep=0.1)
    )
    try:
        job = await ended(lives, asked.id)
    finally:
        _ = living.cancel()
        _ = await asyncio.wait([living])

    assert (job.state, job.outcome) == ("done", {"ended": True})


async def _started(key: str) -> bool:
    return key in STARTED and STARTED[key].is_set()


async def test_a_job_wakes_a_worker_that_waits_for_one(
    opened: Callable[[Handler], PostgresDatabase],
) -> None:
    database = opened(ends_at_once)
    waiting = asyncio.get_running_loop().create_task(
        work(JobClaims(database, SHORT), kinds(ends_at_once), at_once=1, sweep=3600.0)
    )
    try:
        await asyncio.sleep(0.5)
        asked = await database.jobs(SENTINEL_TENANT).ask(A_KIND, {})
        job = await ended(database, asked.id)
    finally:
        _ = waiting.cancel()
        _ = await asyncio.wait([waiting])

    assert job.state == "done"


async def test_a_job_whose_lease_ended_on_each_attempt_is_dead(
    opened: Callable[[Handler], PostgresDatabase],
) -> None:
    database = opened(ends_at_once)
    claims = JobClaims(database, Leases(lease=0.05, renewal=0.01, retry=0.01))
    asked = await database.jobs(SENTINEL_TENANT).ask(A_KIND, {})
    for _ in range(MAX_ATTEMPTS):
        assert await claims.claim([A_KIND]) is not None
        await asyncio.sleep(0.1)

    last = await claims.claim([A_KIND])
    assert last is not None
    await run(claims, kinds(ends_at_once), last)

    job = await database.jobs(SENTINEL_TENANT).read(asked.id)
    assert job is not None
    assert (job.state, job.failure) == ("dead", LEASE_LOST)
