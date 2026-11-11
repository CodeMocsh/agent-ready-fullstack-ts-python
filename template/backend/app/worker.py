"""The worker process: `python -m app.worker`. It claims jobs from Postgres, runs each through the
handler `app/jobs.py` names, and serves no request. The web process, `app.main:app`, starts no
job loop. `docs/adr/template/0027`.

`AT_ONCE` slots each claim one job at a time, and a slot that claims one wakes the others to look
again. A slot that finds no job waits until a job is asked for, which `pg_notify` tells it, until
a job that failed is ready or a lease ends, or until `SWEEP_SECONDS` pass. While a handler runs,
its slot renews the lease. A lease that ends stops the attempt, and the fence keeps the attempt
from writing anything after that. A handler that the database does not serve makes its job ready
again after the refusal's `Retry-After`, and the attempt does not count. SIGTERM and SIGINT cancel
each attempt, and the leases they held end on their own.

Each slot and the `LISTEN` connection are loops of `app.loops.rounds`. In a database outage each
waits and runs again, and the `LISTEN` connection opens again and wakes the slots to look. A step
of an attempt that the database does not serve runs again until the lease would end. When the
database stays unavailable past `UNAVAILABLE_WITHIN_SECONDS`, the worker stops with
`DatabaseGone`.

The memory substrate runs its own jobs, in the web process, so this process refuses it.
"""

import asyncio
import signal
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from app import log
from app.audit_events import acting_as
from app.environment import refuse_development_settings
from app.failing import AttemptsSpent
from app.loop_watch import LoopWatch
from app.loops import DatabaseGone, rounds
from app.pacing import backoff, every
from app.store import DatabaseUnavailable
from app.store.jobs import (
    MAX_ATTEMPTS,
    Asked,
    Claim,
    Deferred,
    Failed,
    LeaseLost,
    attempted,
    fenced,
    handler_of,
)
from app.store.pg import PostgresDatabase
from app.store.pg_jobs import JobClaims
from app.wiring import TelemetrySettings, build, build_service_version, build_telemetry

if TYPE_CHECKING:
    from app.telemetry import Meters

AT_ONCE: Final = 4
"""How many jobs one worker process runs at once."""

SWEEP_SECONDS: Final = 30.0
"""The longest a slot with no job waits before it looks again, with no wake."""


class WorkerNeedsPostgres(RuntimeError):
    """The worker was started on the memory substrate, whose jobs only the web process can see."""


async def work(claims: JobClaims, *, at_once: int = AT_ONCE, sweep: float = SWEEP_SECONDS) -> None:
    """Claim and run the jobs of each kind of `claims.database`, `at_once` at a time, until
    cancelled. Raises `DatabaseGone` when the database stays unavailable past
    `UNAVAILABLE_WITHIN_SECONDS`."""
    wake = asyncio.Event()
    try:
        async with asyncio.TaskGroup() as group:
            _ = group.create_task(rounds("listen", lambda: claims.listen(wake.set)))
            for slot in range(at_once):
                _ = group.create_task(
                    rounds(f"slot {slot}", lambda: _claim_and_run(claims, wake, sweep))
                )
    except* DatabaseGone as gone:
        first = gone.exceptions[0]
        raise first from first.__cause__


async def _claim_and_run(claims: JobClaims, wake: asyncio.Event, sweep: float) -> None:
    kinds = claims.database.kinds
    wake.clear()
    claim = await claims.claim(kinds.keys())
    if claim is None:
        coming = await claims.next_claim_within(kinds.keys())
        await _woken(wake, sweep if coming is None else min(sweep, max(coming, 0.0)))
    else:
        wake.set()
        await run(claims, claim)


async def _woken(wake: asyncio.Event, within: float) -> None:
    try:
        async with asyncio.timeout(within):
            _ = await wake.wait()
    except TimeoutError:
        return


class AttemptAbandoned(RuntimeError):
    """The database did not serve a step of an attempt before the attempt's lease would end."""


@dataclass
class _Held:
    until: float


async def run(claims: JobClaims, claim: Claim) -> None:
    """Run one attempt of the job `claim` holds, and end the job, or make it ready again. A lost
    lease stops the attempt, and `log.lease_lost` names it. A step the database does not serve
    before the lease would end stops it too, and `log.attempt_abandoned` names it: the lease ends,
    and the next claim runs the job again. A step is the read of what the job asked, or a write
    that ends the attempt."""
    held = _Held(claim.held_until)
    try:
        with fenced(claim):
            asked = await _served(held, lambda: claims.asked(claim))
            with acting_as(asked.actor):
                await _attempt(claims, claim, held, asked)
    except LeaseLost:
        log.lease_lost(kind=claim.kind, job=claim.id, attempt=claim.attempts)
    except AttemptAbandoned:
        log.attempt_abandoned(kind=claim.kind, job=claim.id, attempt=claim.attempts)


async def _served[T](held: _Held, step: Callable[[], Awaitable[T]]) -> T:
    while True:
        try:
            return await step()
        except DatabaseUnavailable as unavailable:
            log.database_unavailable(type(unavailable).__name__, unavailable.cause)
            if time.monotonic() + unavailable.retry_after >= held.until:
                raise AttemptAbandoned("the lease ends before the database serves") from unavailable
            await backoff(unavailable.retry_after)


async def _attempt(claims: JobClaims, claim: Claim, held: _Held, asked: Asked) -> None:
    if claim.attempts > MAX_ATTEMPTS:
        await _served(held, lambda: claims.dead(claim, AttemptsSpent.cause))
        log.job_dead(
            kind=claim.kind,
            job=claim.id,
            attempts=MAX_ATTEMPTS,
            cause=AttemptsSpent.cause,
            error=AttemptsSpent.__name__,
        )
        return
    attempt = attempted(
        handler_of(claims.database.kinds, claim.kind),
        claim.tenant_id,
        asked,
        job=claim.id,
        kind=claim.kind,
        attempt=claim.attempts,
    )
    result = await _renewing(claims, claim, held, attempt)
    if isinstance(result, Deferred):
        await _served(held, lambda: claims.deferred(claim, result.after))
    elif not isinstance(result, Failed):
        await _served(held, lambda: claims.done(claim, result))
        log.job_done(kind=claim.kind, job=claim.id, attempt=claim.attempts)
    elif claim.attempts >= MAX_ATTEMPTS:
        await _served(held, lambda: claims.dead(claim, result.cause))
        log.job_dead(
            kind=claim.kind,
            job=claim.id,
            attempts=claim.attempts,
            cause=result.cause,
            error=result.error,
        )
    else:
        await _served(held, lambda: claims.again(claim))


async def _renewing[T](
    claims: JobClaims, claim: Claim, held: _Held, attempt: Coroutine[Any, Any, T]
) -> T:
    try:
        async with asyncio.TaskGroup() as group:
            running = group.create_task(attempt)
            renewer = group.create_task(_renew(claims, claim, held))
            running.add_done_callback(lambda _: renewer.cancel())
    except* LeaseLost as lost:
        raise LeaseLost(f"attempt {claim.attempts} of job {claim.id} lost its lease") from lost
    return running.result()


async def _renew(claims: JobClaims, claim: Claim, held: _Held) -> None:
    async for _ in every(claims.leases.renewal):
        try:
            held.until = await claims.renew(claim)
        except DatabaseUnavailable as unavailable:
            log.database_unavailable(type(unavailable).__name__, unavailable.cause)


async def _main() -> None:
    refuse_development_settings()
    log.configure(build_service_version())
    database = build(serves_requests=False)
    if not isinstance(database, PostgresDatabase):
        raise WorkerNeedsPostgres(
            "DATABASE_URL is unset. The memory substrate runs its own jobs in the web process, "
            "and no other process can see them. Set DATABASE_URL, or run no worker."
        )
    await serve(database, _meters(build_telemetry()))


async def serve(database: PostgresDatabase, meters: "Meters | None") -> None:
    """Check the schema, then claim and run jobs until SIGTERM or SIGINT. With `meters`, report
    the pool, each wait for a connection and how late the event loop ran. Stop `meters`, then
    close `database`."""
    try:
        if meters is not None:
            meters.observe(database)
        version = await database.check()
        log.serving(database.name, version)
        log.working(
            substrate=database.name,
            schema=database.schema,
            kinds=tuple(database.kinds),
            at_once=AT_ONCE,
        )
        await _until_stopped(JobClaims(database), meters)
    finally:
        try:
            if meters is not None:
                await asyncio.to_thread(meters.shutdown)
        finally:
            await database.close()


def _meters(settings: TelemetrySettings | None) -> "Meters | None":
    """The meters that `settings` names, or `None` without settings. Imports `app.telemetry`
    only with settings."""
    if settings is None:
        return None
    from app import telemetry

    return telemetry.meters(settings, telemetry.otlp_metrics(settings))


async def _until_stopped(claims: JobClaims, meters: "Meters | None") -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for stopping in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(stopping, stop.set)
    with LoopWatch(None if meters is None else meters.loop_delay()):
        async with asyncio.TaskGroup() as group:
            working = group.create_task(work(claims))
            _ = await stop.wait()
            _ = working.cancel()


def main() -> None:
    """Run the worker until SIGTERM or SIGINT."""
    asyncio.run(_main())


if __name__ == "__main__":
    main()
