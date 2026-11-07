"""What a worker does to the jobs on Postgres: claim one, hold its lease, and end it.

`job_claims` holds what a claim reads, for every tenant, and no policy scopes it. A claim takes
the next job that is ready, or a job whose lease ended, with `FOR UPDATE SKIP LOCKED`, and sets a
new token. Every transaction of the job then holds that claim with `FOR KEY SHARE`, inside
`app.store.jobs.fenced`, so a worker whose lease ended writes nothing.
`docs/adr/template/0027`.
"""

import json
from collections.abc import AsyncGenerator, Callable, Iterable
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

from app.identity.shared import Actor
from app.models import JsonObject
from app.store.jobs import LEASES, Asked, Claim, LeaseLost, Leases
from app.store.pg import PostgresDatabase, actor_from, wakes

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
    "ready_at = now() + make_interval(secs => $2) WHERE id = $1"
)


class JobClaims:
    """The jobs of every tenant in one schema, as a worker claims them. Each method but `claim`
    and `renew` runs inside `app.store.jobs.fenced`, and raises `LeaseLost` when the claim is no
    longer this attempt's."""

    def __init__(self, database: PostgresDatabase, leases: Leases = LEASES) -> None:
        self.database: PostgresDatabase = database
        self.leases: Leases = leases

    async def claim(self, kinds: Iterable[str]) -> Claim | None:
        """The next job of one of `kinds` that is ready, or whose lease ended, with a new lease
        that ends after `Leases.lease`. `None` when no such job is there. Each claim counts one
        attempt."""
        token = uuid4()
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
        )

    async def renew(self, claim: Claim) -> None:
        """Move the end of the lease of `claim` to `Leases.lease` from now. Raises `LeaseLost`
        when the lease already ended, or another worker claimed the job."""
        async with self.database.connection() as conn:
            renewed = await conn.fetchval(_RENEW, UUID(claim.id), claim.token, self.leases.lease)
        if renewed is None:
            raise LeaseLost(f"attempt {claim.attempts} of job {claim.id} lost its lease")

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
            await conn.execute(_DONE, UUID(claim.id), json.dumps(outcome))
            await conn.execute(_ENDED, UUID(claim.id))

    async def dead(self, claim: Claim, failure: str) -> None:
        """End the job of `claim` as dead, for `failure`."""
        async with self.database.scoped(claim.tenant_id) as conn:
            await conn.execute(_DEAD, UUID(claim.id), failure)
            await conn.execute(_ENDED, UUID(claim.id))

    async def again(self, claim: Claim) -> None:
        """Make the job of `claim` ready again, after `Leases.retry_in` its attempts."""
        async with self.database.scoped(claim.tenant_id) as conn:
            await conn.execute(_AGAIN, UUID(claim.id), self.leases.retry_in(claim.attempts))

    @asynccontextmanager
    async def listening(self, woken: Callable[[], None]) -> AsyncGenerator[None]:
        """Inside the block, call `woken` each time a job is asked for in this schema. The wait is
        a connection of its own, outside the pool, which `app.store.budget.TEMPLATE_IN_PROCESS`
        counts."""
        conn: Any = await self.database.dedicated()
        channel = wakes(self.database.schema)

        def heard(*_: object) -> None:
            woken()

        await conn.add_listener(channel, heard)
        try:
            yield
        finally:
            await conn.close()
