"""What a worker does to the jobs on Postgres: claim one, hold its lease, and end it.

`job_claims` holds what a claim reads, for every tenant, and no policy scopes it. A claim takes
the next job that is ready, or a job whose lease ended, with `FOR UPDATE SKIP LOCKED`, and sets a
new token. Every transaction of the job then holds that claim with `FOR KEY SHARE`, inside
`app.store.jobs.fenced`, so a worker whose lease ended writes nothing.
`docs/adr/template/0027`.
"""

import asyncio
import json
import time
from collections.abc import Callable, Iterable
from typing import Any
from uuid import UUID, uuid4

from app.identity.shared import Actor
from app.models import JsonObject
from app.store.jobs import LEASES, Asked, Claim, LeaseLost, Leases, encoded
from app.store.pg import (
    ConnectionLost,
    PostgresDatabase,
    actor_from,
    failed_on_a_connection,
    wakes,
)

_CLAIM = (
    "UPDATE job_claims SET state = 'running', token = $1, "
    "lease_ends = now() + make_interval(secs => $2), attempts = attempts + 1 "
    "WHERE id = (SELECT id FROM job_claims WHERE kind = ANY($3::text[]) AND ("
    "(state = 'ready' AND ready_at <= now()) OR (state = 'running' AND lease_ends <= now())) "
    "ORDER BY ready_at LIMIT 1 FOR UPDATE SKIP LOCKED) "
    "RETURNING id, tenant_id, kind, attempts"
)
_RENEW = (
    "UPDATE job_claims SET lease_ends = now() + make_interval(secs => $3) "
    "WHERE id = $1 AND token = $2 AND state = 'running' AND lease_ends > now() RETURNING id"
)
_ASKED = (
    "SELECT input, actor_issuer, actor_subject, actor_name, on_behalf_of_issuer, "
    "on_behalf_of_subject, on_behalf_of_name FROM jobs WHERE id = $1"
)
_DONE = "UPDATE jobs SET state = 'done', outcome = $2::jsonb WHERE id = $1"
_DEAD = "UPDATE jobs SET state = 'dead', failure = $2 WHERE id = $1"
_ENDED = "UPDATE job_claims SET state = 'ended', token = NULL, lease_ends = NULL WHERE id = $1"
_AGAIN = (
    "UPDATE job_claims SET state = 'ready', token = NULL, lease_ends = NULL, "
    "ready_at = now() + make_interval(secs => $2), attempts = attempts - $3 WHERE id = $1"
)
_NEXT = (
    "SELECT extract(epoch FROM min(next) - now()) FROM ("
    "SELECT ready_at AS next FROM job_claims WHERE state = 'ready' "
    "AND kind = ANY($1::text[]) UNION ALL "
    "SELECT lease_ends FROM job_claims WHERE state = 'running' AND lease_ends > now() "
    "AND kind = ANY($1::text[])) AS later"
)


class JobClaims:
    """The jobs of every tenant in one schema, as a worker claims them. `asked`, `done`, `dead`,
    `again` and `deferred` run inside `app.store.jobs.fenced`, and raise `LeaseLost` when the
    claim is no longer this attempt's."""

    def __init__(self, database: PostgresDatabase, leases: Leases = LEASES) -> None:
        self.database: PostgresDatabase = database
        self.leases: Leases = leases

    async def claim(self, kinds: Iterable[str]) -> Claim | None:
        """The next job of one of `kinds` that is ready, or whose lease ended, with a new lease
        that ends after `Leases.lease`. `None` when no such job is there. Each claim counts one
        attempt."""
        token = uuid4()
        asked_at = time.monotonic()
        async with self.database.connection() as conn, conn.transaction():
            row = await conn.fetchrow(_CLAIM, token, self.leases.lease, list(kinds))
        if row is None:
            return None
        return Claim(
            id=str(row["id"]),
            tenant_id=row["tenant_id"],
            kind=row["kind"],
            token=token,
            attempts=row["attempts"],
            held_until=asked_at + self.leases.lease,
        )

    async def renew(self, claim: Claim) -> float:
        """Move the end of the lease of `claim` to `Leases.lease` from now, and return a time on
        this process's `time.monotonic` clock at or before that end. Raises `LeaseLost` when the
        lease already ended, or another worker claimed the job."""
        asked_at = time.monotonic()
        async with self.database.connection() as conn:
            renewed = await conn.fetchval(_RENEW, UUID(claim.id), claim.token, self.leases.lease)
        if renewed is None:
            raise LeaseLost(f"attempt {claim.attempts} of job {claim.id} lost its lease")
        return asked_at + self.leases.lease

    async def asked(self, claim: Claim) -> Asked:
        """What the tenant of `claim` asked: the job's input, and the actor that asked."""
        async with self.database.scoped(claim.tenant_id) as conn:
            row = await conn.fetchrow(_ASKED, UUID(claim.id))
        return Asked(
            input=json.loads(row["input"]),
            actor=Actor(
                issuer=row["actor_issuer"],
                subject=row["actor_subject"],
                name=row["actor_name"],
                on_behalf_of=actor_from(
                    row["on_behalf_of_issuer"],
                    row["on_behalf_of_subject"],
                    row["on_behalf_of_name"],
                ),
            ),
        )

    async def done(self, claim: Claim, outcome: JsonObject) -> None:
        """End the job of `claim` with `outcome`."""
        async with self.database.scoped(claim.tenant_id) as conn:
            await conn.execute(_DONE, UUID(claim.id), encoded(outcome))
            await conn.execute(_ENDED, UUID(claim.id))

    async def dead(self, claim: Claim, failure: str) -> None:
        """End the job of `claim` as dead, for `failure`."""
        async with self.database.scoped(claim.tenant_id) as conn:
            await conn.execute(_DEAD, UUID(claim.id), failure)
            await conn.execute(_ENDED, UUID(claim.id))

    async def again(self, claim: Claim) -> None:
        """Make the job of `claim` ready again, after `Leases.retry_in` its attempts."""
        async with self.database.scoped(claim.tenant_id) as conn:
            await conn.execute(_AGAIN, UUID(claim.id), self.leases.retry_in(claim.attempts), 0)

    async def deferred(self, claim: Claim, after: float) -> None:
        """Make the job of `claim` ready again after `after` seconds, and do not count the
        attempt."""
        async with self.database.scoped(claim.tenant_id) as conn:
            await conn.execute(_AGAIN, UUID(claim.id), after, 1)

    async def next_claim_within(self, kinds: Iterable[str]) -> float | None:
        """The seconds until a job of one of `kinds` is ready, or a lease of one ends: the next
        claim that time alone makes possible. Zero or less when a job is ready now. `None` when
        none is coming."""
        async with self.database.connection() as conn:
            seconds = await conn.fetchval(_NEXT, list(kinds))
        return None if seconds is None else float(seconds)

    async def listen(self, woken: Callable[[], None]) -> None:
        """Call `woken` once the `LISTEN` connection opens, and each time a job is asked for in this
        schema. That connection is its own, outside the pool, and
        `app.store.budget.TEMPLATE_IN_PROCESS` counts it. Returns when it ends after it heard for
        `retry_after` seconds or more. Raises `ConnectionRefused` when it does not open, and
        `ConnectionLost` when it ends sooner."""
        conn: Any = await self.database.dedicated()
        opened = time.monotonic()
        ended = asyncio.Event()

        def heard(*_: object) -> None:
            woken()

        def lost(_: object) -> None:
            ended.set()

        conn.add_termination_listener(lost)
        try:
            await self._listening(conn, heard)
            woken()
            _ = await ended.wait()
        finally:
            conn.remove_termination_listener(lost)
            await conn.close()
        if time.monotonic() - opened < self.database.retry_after:
            raise self._lost(f"ended within {self.database.retry_after} s of opening")

    async def _listening(self, conn: Any, heard: Callable[..., None]) -> None:
        try:
            await conn.add_listener(wakes(self.database.schema), heard)
        except failed_on_a_connection() as failed:
            if not conn.is_closed():
                raise
            raise self._lost(f"ended before it listened: {type(failed).__name__}") from failed

    def _lost(self, how: str) -> ConnectionLost:
        return ConnectionLost(f"the LISTEN connection {how}", retry_after=self.database.retry_after)
