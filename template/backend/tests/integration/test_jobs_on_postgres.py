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
from typing import Any, Final
from uuid import UUID

import pytest

from app import log
from app.audit_events import acting_as
from app.failing import AttemptsSpent, Internal
from app.identity.shared import SENTINEL_TENANT, Actor
from app.models import CreateTaskBody, Failure, Job, JsonObject
from app.store.jobs import (
    MAX_ATTEMPTS,
    Claim,
    Handler,
    Kinds,
    LeaseLost,
    Leases,
    fenced,
)
from app.store.pg import ConnectionLost, PostgresDatabase, Timeouts
from app.store.pg_jobs import JobClaims
from app.worker import run, work
from tests.clock import elapse
from tests.conftest import Logged
from tests.integration.conftest import ONE_PROCESS, Provisioned, acting_on, connect, end_session
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
    assert (job.state, job.failure) == ("dead", Failure(cause=AttemptsSpent.cause))


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


async def test_a_job_that_is_ready_again_after_its_claim_takes_no_ask_once_per_its_key(
    opened: Callable[[Handler], PostgresDatabase],
) -> None:
    database = opened(ends_at_once)
    jobs = database.jobs(SENTINEL_TENANT)
    claims = JobClaims(database, LONG)
    first = await jobs.ask(A_KIND, {"key": "again"}, once_per="again")
    claim = await claims.claim(database.kinds)
    assert claim is not None
    assert claim.id == first.id

    await claims.again(claim)
    later = await jobs.ask(A_KIND, {"key": "again"}, once_per="again")
    joined = await jobs.ask(A_KIND, {"key": "again"}, once_per="again")

    assert later.id != first.id
    assert joined.id == later.id


async def test_asks_once_per_a_key_at_the_same_time_are_one_job(
    opened: Callable[[Handler], PostgresDatabase],
) -> None:
    jobs = opened(ends_at_once).jobs(SENTINEL_TENANT)

    asked = await asyncio.gather(
        *(jobs.ask(A_KIND, {"key": "at once"}, once_per="at once") for _ in range(8))
    )

    assert len({job.id for job in asked}) == 1


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

    assert (job.state, job.failure) == ("dead", Failure(cause=Internal.cause))
    assert still_working


QUICK: Final = Timeouts(acquire=1.0)
"""Bounds whose `Retry-After` is a second, so a test waits out an outage quickly."""

PIDS: Final[dict[str, int]] = {}
"""The session of the attempt of the job with the key in its input."""


def sleeps_until_ended(database: PostgresDatabase) -> Handler:
    """A handler whose first attempt sleeps in a transaction of the job until the test ends its
    session, and whose next attempt ends the job."""

    async def handler(tenant_id: str, input: JsonObject) -> JsonObject:
        key = str(input["key"])
        if key in STARTED:
            return {"ended": True}
        STARTED[key] = asyncio.Event()
        async with database.scoped(tenant_id) as conn:
            PIDS[key] = await conn.fetchval("SELECT pg_backend_pid()")
            STARTED[key].set()
            await conn.execute("SELECT pg_sleep(3)")
        raise AssertionError("the session of the first attempt did not end")

    return handler


RELEASED: Final[dict[str, asyncio.Event]] = {}
"""Set to let the first attempt of the job with the key in its input end."""


async def ends_once_released(_tenant_id: str, input: JsonObject) -> JsonObject:
    """A handler whose first attempt ends when the test releases it, and whose next one at once."""
    key = str(input["key"])
    if key not in STARTED:
        STARTED[key] = asyncio.Event()
        STARTED[key].set()
        _ = await RELEASED.setdefault(key, asyncio.Event()).wait()
    return {"ended": True}


QuicklyOpened = Callable[[Callable[[PostgresDatabase], Handler]], PostgresDatabase]


@pytest.fixture
async def quickly(provisioned: Provisioned) -> AsyncIterator[QuicklyOpened]:
    """Each call opens a database under `QUICK`, whose jobs of `A_KIND` the handler that the
    call builds from it runs."""
    held: list[PostgresDatabase] = []

    def opening(handler: Callable[[PostgresDatabase], Handler]) -> PostgresDatabase:
        database = PostgresDatabase(
            dsn=provisioned.app_dsn,
            schema=provisioned.schema,
            draws=ONE_PROCESS,
            kinds=lambda opened: {A_KIND: handler(opened)},
            timeouts=QUICK,
        )
        held.append(database)
        return database

    try:
        yield opening
    finally:
        for database in held:
            await database.close()


@pytest.fixture
def quick(quickly: QuicklyOpened) -> PostgresDatabase:
    """A database under `QUICK`, whose jobs of `A_KIND` `sleeps_until_ended` runs."""
    return quickly(sleeps_until_ended)


async def _listening(provisioned: Provisioned) -> set[int]:
    admin = await connect(provisioned.admin_dsn)
    try:
        rows = await admin.fetch(
            "SELECT pid FROM pg_stat_activity WHERE usename = $1 AND query LIKE 'LISTEN %'",
            provisioned.app,
        )
    finally:
        await admin.close()
    return {row["pid"] for row in rows}


async def _end_every_session(provisioned: Provisioned, *, then_refuse: bool = False) -> int:
    """End each session of the application role, as a restart of Postgres does. With
    `then_refuse`, the role gets no new session until `_admit` runs."""
    admin = await connect(provisioned.admin_dsn)
    try:
        if then_refuse:
            await admin.execute(f'ALTER ROLE "{provisioned.app}" CONNECTION LIMIT 0')
        return await admin.fetchval(
            "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE usename = $1",
            provisioned.app,
        )
    finally:
        await admin.close()


async def _admit(provisioned: Provisioned) -> None:
    admin = await connect(provisioned.admin_dsn)
    try:
        await admin.execute(f'ALTER ROLE "{provisioned.app}" CONNECTION LIMIT -1')
    finally:
        await admin.close()


async def _heard_until_ended(
    quick: PostgresDatabase, provisioned: Provisioned, *, heard_for: float
) -> asyncio.Task[None]:
    listening = asyncio.get_running_loop().create_task(JobClaims(quick).listen(lambda: None))
    await until(lambda: _listening_again(provisioned, set()))
    await elapse(heard_for)
    [pid] = await _listening(provisioned)
    await end_session(provisioned.admin_dsn, pid)
    _ = await asyncio.wait([listening])
    return listening


async def test_a_listen_connection_that_heard_and_then_ended_ends_its_round(
    quick: PostgresDatabase, provisioned: Provisioned
) -> None:
    listened = await _heard_until_ended(quick, provisioned, heard_for=QUICK.acquire * 1.5)

    assert listened.exception() is None


async def test_a_listen_connection_that_ends_at_once_is_lost(
    quick: PostgresDatabase, provisioned: Provisioned
) -> None:
    listened = await _heard_until_ended(quick, provisioned, heard_for=0)

    assert isinstance(listened.exception(), ConnectionLost)


async def test_a_worker_whose_sessions_all_end_hears_again_and_ends_the_next_job(
    opened: Callable[[Handler], PostgresDatabase], provisioned: Provisioned
) -> None:
    database = opened(ends_at_once)
    working = await _working(database)
    try:
        before = await _listening_now(provisioned)
        ended_sessions = await _end_every_session(provisioned)
        await until(lambda: _listening_again(provisioned, before))
        asked = await database.jobs(SENTINEL_TENANT).ask(A_KIND, {})
        job = await ended(database, asked.id)
        still_working = not working.done()
    finally:
        await _stopped(working)

    assert ended_sessions >= 2
    assert job.state == "done"
    assert still_working


async def _listening_now(provisioned: Provisioned) -> set[int]:
    """The `LISTEN` session of the worker, once it listens."""
    await until(lambda: _listening_again(provisioned, set()))
    return await _listening(provisioned)


async def _listening_again(provisioned: Provisioned, before: set[int]) -> bool:
    return (await _listening(provisioned) - before) != set()


async def test_a_worker_waits_out_an_outage_and_then_ends_a_job(
    quick: PostgresDatabase, provisioned: Provisioned, logged: Logged
) -> None:
    log.configure(None)
    working = await _working(quick)
    try:
        before = await _listening_now(provisioned)
        _ = await _end_every_session(provisioned, then_refuse=True)
        await elapse(QUICK.acquire * 2.5)
        await _admit(provisioned)
        await until(lambda: _listening_again(provisioned, before))
        asked = await quick.jobs(SENTINEL_TENANT).ask(A_KIND, {"key": "after an outage"})
        job = await ended(quick, asked.id)
        still_working = not working.done()
    finally:
        await _admit(provisioned)
        await _stopped(working)

    waits = [
        (line["loop"], line["refusal"], line["cause"])
        for line in logged()
        if line["message"] == "loop waits for the database"
    ]
    assert ("listen", "ConnectionRefused", "TooManyConnectionsError") in waits
    assert job.state == "done"
    assert still_working


async def test_an_attempt_whose_session_ends_is_deferred_and_does_not_count(
    quick: PostgresDatabase, provisioned: Provisioned
) -> None:
    key = "its session ends"
    working = await _working(quick)
    try:
        asked = await quick.jobs(SENTINEL_TENANT).ask(A_KIND, {"key": key})
        await until(lambda: _started(key))
        await end_session(provisioned.admin_dsn, PIDS[key])
        job = await ended(quick, asked.id)
    finally:
        await _stopped(working)

    assert (job.state, job.outcome) == ("done", {"ended": True})
    assert await _attempts_of(provisioned, asked.id) == 1


async def _attempts_of(provisioned: Provisioned, id: str) -> int:
    admin = await connect(provisioned.admin_dsn)
    try:
        return await admin.fetchval(
            f'SELECT attempts FROM "{provisioned.schema}".job_claims WHERE id = $1', UUID(id)
        )
    finally:
        await admin.close()


@pytest.mark.parametrize(
    ("lease", "attempts", "abandoned"),
    [(10.0, 1, []), (1.5, 2, ["attempt abandoned"])],
    ids=["served within the lease", "not served within the lease"],
)
async def test_the_end_of_an_attempt_runs_again_until_its_lease_would_end(
    quickly: QuicklyOpened,
    provisioned: Provisioned,
    logged: Logged,
    lease: float,
    attempts: int,
    abandoned: list[str],
) -> None:
    log.configure(None)
    key = f"its end waits out an outage under a lease of {lease} s"
    database = quickly(lambda _: ends_once_released)
    working = await _working(database, Leases(lease=lease, renewal=lease / 4, retry=0.01))
    try:
        asked = await database.jobs(SENTINEL_TENANT).ask(A_KIND, {"key": key})
        await until(lambda: _started(key))
        _ = await _end_every_session(provisioned, then_refuse=True)
        RELEASED.setdefault(key, asyncio.Event()).set()
        await elapse(QUICK.acquire * 2.5)
        await _admit(provisioned)
        job = await ended(database, asked.id)
        still_working = not working.done()
    finally:
        await _admit(provisioned)
        await _stopped(working)

    assert (job.state, job.outcome) == ("done", {"ended": True})
    assert await _attempts_of(provisioned, asked.id) == attempts
    assert [line["message"] for line in logged() if line["message"] == "attempt abandoned"] == (
        abandoned
    )
    assert still_working


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


async def test_a_job_that_an_earlier_release_ended_reads_as_a_cause(
    opened: Callable[[Handler], PostgresDatabase], provisioned: Provisioned
) -> None:
    """A worker of a release before 0.76.0 stores the class of the error. It can still end a job
    while the next release serves, and after a rollback. `docs/adr/template/0031`."""
    jobs = opened(ends_at_once).jobs(SENTINEL_TENANT)
    spent, raised = await jobs.ask(A_KIND, {}), await jobs.ask(A_KIND, {})
    conn: Any = await acting_on(await connect(provisioned.admin_dsn))
    try:
        for job, failure in ((spent, "AttemptsSpent"), (raised, "ChildFailed")):
            await conn.execute(
                f"UPDATE \"{provisioned.schema}\".jobs SET state = 'dead', failure = $2"
                " WHERE id = $1",
                UUID(job.id),
                failure,
            )
    finally:
        await conn.close()

    read = [await jobs.read(job.id) for job in (spent, raised)]

    assert [None if job is None else job.failure for job in read] == [
        Failure(cause=AttemptsSpent.cause),
        Failure(cause=Internal.cause),
    ]
