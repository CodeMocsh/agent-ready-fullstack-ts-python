"""What only Postgres keeps of the jobs contract: the fence, the lease, the wake, and a second
process. `docs/adr/template/0027`.

`python -m app.worker` itself, on the example's kind, is in
`tests/integration/test_tasks_on_postgres.py`. A worker dies here as a cancelled task: it
releases nothing, as a killed process releases nothing, so another worker gets the job only when
the lease ends.
"""

import asyncio
import math
import os
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path
from typing import Final

import pytest

from app.audit_events import acting_as
from app.identity.shared import SENTINEL_TENANT, Actor
from app.models import CreateTaskBody, Job, JsonObject
from app.store.jobs import (
    ATTEMPTS_SPENT,
    MAX_ATTEMPTS,
    Claim,
    Handler,
    Kinds,
    LeaseLost,
    Leases,
    fenced,
)
from app.store.pg import PostgresDatabase
from app.store.pg_jobs import JobClaims, WakesLost
from app.worker import run, work
from tests.clock import elapse
from tests.integration.conftest import ONE_PROCESS, Provisioned, connect
from tests.jobs_contract import ASKER, OTHER_TENANT, contract_kinds
from tests.pages import every_task

SHORT: Final = Leases(lease=1.0, renewal=0.2, retry=0.01)
"""A lease that ends a second after nobody renews it."""

LONG: Final = Leases(lease=60.0, renewal=20.0, retry=0.01)
"""A lease that outlasts any test."""

WITHIN: Final = 15.0
"""The longest a test waits for a job to end."""

STARTED: Final[dict[str, asyncio.Event]] = {}
"""Set when an attempt of the job with the key in its input starts."""


async def starts_and_waits(_tenant_id: str, input: JsonObject) -> JsonObject:
    STARTED.setdefault(str(input["key"]), asyncio.Event()).set()
    _ = await asyncio.Event().wait()
    return {}


async def ends_at_once(_tenant_id: str, _input: JsonObject) -> JsonObject:
    return {"ended": True}


async def waits_past_its_lease(_tenant_id: str, _input: JsonObject) -> JsonObject:
    await elapse(SHORT.lease * 2.5)
    return {"ended": True}


async def fails_once(_tenant_id: str, input: JsonObject) -> JsonObject:
    key = str(input["key"])
    if key not in STARTED:
        STARTED[key] = asyncio.Event()
        raise ConnectionError("the first attempt fails")
    return {"ended": True}


async def answers_what_cannot_be_stored(_tenant_id: str, _input: JsonObject) -> JsonObject:
    return {"ratio": math.nan}


A_KIND: Final = "a kind"

BACKEND: Final = Path(__file__).resolve().parents[2]


def kinds(handler: Handler) -> Callable[[PostgresDatabase], Kinds]:
    return lambda _database: {A_KIND: handler}


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
            await elapse(0.05)


async def until(condition: Callable[[], Awaitable[bool]]) -> None:
    async with asyncio.timeout(WITHIN):
        while not await condition():
            await elapse(0.02)


async def test_an_attempt_whose_lease_ended_writes_nothing_after_another_worker_claims_the_job(
    opened: Callable[[Handler], PostgresDatabase],
) -> None:
    database = opened(ends_at_once)
    claims = JobClaims(database, SHORT)
    _ = await database.jobs(SENTINEL_TENANT).ask(A_KIND, {})
    slow = await claims.claim([A_KIND])
    assert slow is not None
    await elapse(SHORT.lease + 0.2)

    fast = await claims.claim([A_KIND])

    assert fast is not None
    assert (fast.id, fast.attempts) == (slow.id, 2)
    with fenced(slow), pytest.raises(LeaseLost):
        _ = await database.store(SENTINEL_TENANT).create(CreateTaskBody(title="too late"))
    assert await every_task(database.store(SENTINEL_TENANT)) == []


async def test_a_transaction_of_a_job_for_another_tenant_writes_nothing(
    opened: Callable[[Handler], PostgresDatabase],
) -> None:
    database = opened(ends_at_once)
    _ = await database.jobs(SENTINEL_TENANT).ask(A_KIND, {})
    held = await JobClaims(database, LONG).claim([A_KIND])
    assert held is not None

    with fenced(held), pytest.raises(LeaseLost):
        _ = await database.store(OTHER_TENANT).create(CreateTaskBody(title="another tenant's"))
    assert await every_task(database.store(OTHER_TENANT)) == []


async def test_a_job_whose_transaction_is_open_is_not_claimed_when_its_lease_ends(
    opened: Callable[[Handler], PostgresDatabase],
) -> None:
    database = opened(ends_at_once)
    claims = JobClaims(database, SHORT)
    _ = await database.jobs(SENTINEL_TENANT).ask(A_KIND, {})
    held = await claims.claim([A_KIND])
    assert held is not None

    with fenced(held):
        async with database.scoped(SENTINEL_TENANT):
            await elapse(SHORT.lease + 0.2)
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
    dying = asyncio.get_running_loop().create_task(work(JobClaims(dies, SHORT), at_once=1))
    await until(lambda: _started(key))
    _ = dying.cancel()
    _ = await asyncio.wait([dying])

    lives = opened(ends_at_once)
    living = asyncio.get_running_loop().create_task(
        work(JobClaims(lives, SHORT), at_once=1, sweep=0.1)
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
        work(JobClaims(database, SHORT), at_once=1, sweep=3600.0)
    )
    try:
        await elapse(0.5)
        asked = await database.jobs(SENTINEL_TENANT).ask(A_KIND, {})
        job = await ended(database, asked.id)
    finally:
        _ = waiting.cancel()
        _ = await asyncio.wait([waiting])

    assert job.state == "done"


async def test_a_job_whose_attempts_are_spent_with_no_outcome_is_dead(
    opened: Callable[[Handler], PostgresDatabase],
) -> None:
    database = opened(ends_at_once)
    ending = Leases(lease=0.05, renewal=0.01, retry=0.01)
    asked = await database.jobs(SENTINEL_TENANT).ask(A_KIND, {})
    for _ in range(MAX_ATTEMPTS):
        assert await JobClaims(database, ending).claim([A_KIND]) is not None
        await elapse(ending.lease * 2)

    claims = JobClaims(database, LONG)
    last = await claims.claim([A_KIND])
    assert last is not None
    await run(claims, last)

    job = await database.jobs(SENTINEL_TENANT).read(asked.id)
    assert job is not None
    assert (job.state, job.failure) == ("dead", ATTEMPTS_SPENT)


async def _working(database: PostgresDatabase, leases: Leases = SHORT) -> asyncio.Task[None]:
    task = asyncio.get_running_loop().create_task(
        work(JobClaims(database, leases), at_once=1, sweep=3600.0)
    )
    await elapse(0.3)
    return task


async def _stopped(task: asyncio.Task[None]) -> None:
    _ = task.cancel()
    _ = await asyncio.wait([task])


async def test_a_handler_that_runs_past_its_lease_keeps_its_claim(
    opened: Callable[[Handler], PostgresDatabase],
) -> None:
    database = opened(waits_past_its_lease)
    working = await _working(database)
    try:
        asked = await database.jobs(SENTINEL_TENANT).ask(A_KIND, {})
        await elapse(SHORT.lease * 1.5)
        taken = await JobClaims(database, SHORT).claim([A_KIND])
        job = await ended(database, asked.id)
    finally:
        await _stopped(working)

    assert taken is None
    assert (job.state, job.outcome) == ("done", {"ended": True})


@pytest.mark.parametrize("ending", ["done", "dead", "again", "deferred"])
async def test_an_end_whose_lease_ended_writes_nothing_after_another_worker_claims_the_job(
    opened: Callable[[Handler], PostgresDatabase], ending: str
) -> None:
    database = opened(ends_at_once)
    claims = JobClaims(database, SHORT)
    asked = await database.jobs(SENTINEL_TENANT).ask(A_KIND, {})
    slow = await claims.claim([A_KIND])
    assert slow is not None
    await elapse(SHORT.lease + 0.2)
    assert await JobClaims(database, LONG).claim([A_KIND]) is not None
    ends: dict[str, Callable[[Claim], Awaitable[None]]] = {
        "done": lambda claim: claims.done(claim, {"ended": True}),
        "dead": lambda claim: claims.dead(claim, "TooLate"),
        "again": claims.again,
        "deferred": lambda claim: claims.deferred(claim, 0.01),
    }

    end = ends[ending]
    with fenced(slow), pytest.raises(LeaseLost):
        await end(slow)

    job = await database.jobs(SENTINEL_TENANT).read(asked.id)
    assert job is not None
    assert job.state == "pending"
    assert await claims.claim([A_KIND]) is None


async def test_a_job_that_failed_is_tried_again_once_its_delay_has_passed(
    opened: Callable[[Handler], PostgresDatabase],
) -> None:
    database = opened(fails_once)
    delayed = Leases(lease=5.0, renewal=1.0, retry=0.5)
    working = await _working(database, delayed)
    try:
        asked = await database.jobs(SENTINEL_TENANT).ask(A_KIND, {"key": "tried again"})
        started = asyncio.get_running_loop().time()
        job = await ended(database, asked.id)
        waited = asyncio.get_running_loop().time() - started
    finally:
        await _stopped(working)

    assert job.state == "done"
    assert delayed.retry_in(1) <= waited < WITHIN


async def test_an_outcome_that_cannot_be_stored_fails_its_attempt_and_not_the_worker(
    opened: Callable[[Handler], PostgresDatabase],
) -> None:
    database = opened(answers_what_cannot_be_stored)
    working = await _working(database)
    try:
        asked = await database.jobs(SENTINEL_TENANT).ask(A_KIND, {})
        job = await ended(database, asked.id)
        still_working = not working.done()
    finally:
        await _stopped(working)

    assert (job.state, job.failure) == ("dead", "UnstorableJson")
    assert still_working


async def test_a_worker_whose_wakes_end_stops(
    opened: Callable[[Handler], PostgresDatabase], provisioned: Provisioned
) -> None:
    working = await _working(opened(ends_at_once))
    admin = await connect(provisioned.admin_dsn)
    try:
        ended_backends = await admin.fetchval(
            "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
            "WHERE usename = $1 AND query LIKE 'LISTEN %'",
            provisioned.app,
        )
    finally:
        await admin.close()

    assert ended_backends == 1
    with pytest.raises(WakesLost):
        async with asyncio.timeout(WITHIN):
            await working


async def test_a_worker_in_another_process_ends_a_job_that_this_process_asked_for(
    provisioned: Provisioned,
) -> None:
    database = PostgresDatabase(
        dsn=provisioned.app_dsn, schema=provisioned.schema, draws=ONE_PROCESS, kinds=contract_kinds
    )
    worker = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import asyncio, sys\n"
        "from app.store.pg import PostgresDatabase\n"
        "from app.store.pg_jobs import JobClaims\n"
        "from app.worker import work\n"
        "from tests.integration.conftest import ONE_PROCESS\n"
        "from tests.jobs_contract import contract_kinds\n"
        "database = PostgresDatabase(dsn=sys.argv[1], schema=sys.argv[2], draws=ONE_PROCESS, "
        "kinds=contract_kinds)\n"
        "asyncio.run(work(JobClaims(database), sweep=3600.0))\n",
        provisioned.app_dsn,
        provisioned.schema,
        cwd=BACKEND,
        env={**os.environ, "APP_ENV": "development"},
        stdout=asyncio.subprocess.DEVNULL,
    )
    try:
        await elapse(2.0)
        asked = await database.jobs(SENTINEL_TENANT).ask("creates a task", {"title": "elsewhere"})
        job = await ended(database, asked.id)
        tasks = await every_task(database.store(SENTINEL_TENANT))
    finally:
        worker.kill()
        _ = await worker.wait()
        await database.close()

    assert job.state == "done"
    assert job.outcome is not None
    assert [task.id for task in tasks] == [job.outcome["id"]]
