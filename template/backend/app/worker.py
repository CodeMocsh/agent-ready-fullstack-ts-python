"""The worker process: `python -m app.worker`. It claims jobs from Postgres, runs each through the
handler `app/jobs.py` names, and serves no request. The web process, `app.main:app`, starts no
job loop. `docs/adr/template/0027`.

`AT_ONCE` slots each claim one job at a time. A slot that finds no job waits until a job is asked
for, which `pg_notify` tells it, or until `SWEEP_SECONDS` pass, which also finds each lease that
ended. While a handler runs, its slot renews the lease. A lease that ends stops the attempt, and
the fence keeps the attempt from writing anything after that. SIGTERM and SIGINT cancel each
attempt, and the leases they held end on their own.

The memory substrate runs its own jobs, in the web process, so this process refuses it.
"""

import asyncio
import signal
from collections.abc import Coroutine
from typing import Any, Final

from app import jobs, log
from app.audit_events import acting_as
from app.environment import refuse_development_settings
from app.loop_watch import LoopWatch
from app.store import DatabaseBusy
from app.store.jobs import (
    LEASE_LOST,
    MAX_ATTEMPTS,
    Asked,
    Claim,
    Failed,
    Kinds,
    LeaseLost,
    attempted,
    fenced,
    handler_of,
    store_of_the_job,
)
from app.store.pg import PostgresDatabase
from app.store.pg_jobs import JobClaims
from app.wiring import build, build_service_version

AT_ONCE: Final = 4
"""How many jobs one worker process runs at once."""

SWEEP_SECONDS: Final = 30.0
"""The longest a slot with no job waits before it looks again, with no wake."""


class WorkerNeedsPostgres(RuntimeError):
    """The worker was started on the memory substrate, whose jobs only the web process can see."""


async def work(
    claims: JobClaims, kinds: Kinds, *, at_once: int = AT_ONCE, sweep: float = SWEEP_SECONDS
) -> None:
    """Claim and run the jobs of `kinds`, `at_once` at a time, until cancelled."""
    wake = asyncio.Event()
    async with claims.listening(wake.set), asyncio.TaskGroup() as group:
        for _ in range(at_once):
            _ = group.create_task(_slot(claims, kinds, wake, sweep))


async def _slot(claims: JobClaims, kinds: Kinds, wake: asyncio.Event, sweep: float) -> None:
    while True:
        wake.clear()
        try:
            claim = await claims.claim(kinds.keys())
            if claim is None:
                await _woken(wake, sweep)
            else:
                await run(claims, kinds, claim)
        except DatabaseBusy as busy:
            log.database_busy(type(busy).__name__)
            await asyncio.sleep(busy.retry_after)


async def _woken(wake: asyncio.Event, within: float) -> None:
    try:
        async with asyncio.timeout(within):
            _ = await wake.wait()
    except TimeoutError:
        return


async def run(claims: JobClaims, kinds: Kinds, claim: Claim) -> None:
    """Run one attempt of the job `claim` holds, and end the job, or make it ready again. A lost
    lease stops the attempt, and `log.lease_lost` names it."""
    try:
        with fenced(claim):
            asked = await claims.asked(claim)
            with acting_as(asked.actor):
                await _attempt(claims, kinds, claim, asked)
    except LeaseLost:
        log.lease_lost(kind=claim.kind, job=claim.id, attempt=claim.attempts)


async def _attempt(claims: JobClaims, kinds: Kinds, claim: Claim, asked: Asked) -> None:
    if claim.attempts > MAX_ATTEMPTS:
        await claims.dead(claim, LEASE_LOST)
        log.job_dead(kind=claim.kind, job=claim.id, attempts=MAX_ATTEMPTS, failure=LEASE_LOST)
        return
    attempt = attempted(
        handler_of(kinds, claim.kind),
        store_of_the_job(claims.database, claim.tenant_id),
        asked,
        job=claim.id,
        kind=claim.kind,
        attempt=claim.attempts,
    )
    result = await _renewing(claims, claim, attempt)
    if not isinstance(result, Failed):
        await claims.done(claim, result)
        log.job_done(kind=claim.kind, job=claim.id, attempt=claim.attempts)
    elif claim.attempts >= MAX_ATTEMPTS:
        await claims.dead(claim, result.failure)
        log.job_dead(kind=claim.kind, job=claim.id, attempts=claim.attempts, failure=result.failure)
    else:
        await claims.again(claim)


async def _renewing[T](claims: JobClaims, claim: Claim, attempt: Coroutine[Any, Any, T]) -> T:
    try:
        async with asyncio.TaskGroup() as group:
            running = group.create_task(attempt)
            renewer = group.create_task(_renew(claims, claim))
            running.add_done_callback(lambda _: renewer.cancel())
    except* LeaseLost as lost:
        raise LeaseLost(f"attempt {claim.attempts} of job {claim.id} lost its lease") from lost
    return running.result()


async def _renew(claims: JobClaims, claim: Claim) -> None:
    while True:
        await asyncio.sleep(claims.leases.renewal)
        try:
            await claims.renew(claim)
        except DatabaseBusy as busy:
            log.database_busy(type(busy).__name__)


async def _main() -> None:
    refuse_development_settings()
    log.configure(build_service_version())
    database = build(serves_requests=False)
    if not isinstance(database, PostgresDatabase):
        raise WorkerNeedsPostgres(
            "DATABASE_URL is unset. The memory substrate runs its own jobs in the web process, "
            "and no other process can see them. Set DATABASE_URL, or run no worker."
        )
    try:
        version = await database.check()
        log.serving(database.name, version)
        log.working(
            substrate=database.name,
            schema=database.schema,
            kinds=tuple(jobs.KINDS),
            at_once=AT_ONCE,
        )
        await _until_stopped(JobClaims(database), jobs.KINDS)
    finally:
        await database.close()


async def _until_stopped(claims: JobClaims, kinds: Kinds) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for stopping in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(stopping, stop.set)
    with LoopWatch(None):
        async with asyncio.TaskGroup() as group:
            working = group.create_task(work(claims, kinds))
            _ = await stop.wait()
            _ = working.cancel()


def main() -> None:
    """Run the worker until SIGTERM or SIGINT."""
    asyncio.run(_main())


if __name__ == "__main__":
    main()
