"""The jobs contract, against a real Postgres, where a worker ends each job.

`finish` claims and runs each job as `app.worker` does, in this process, until no job is left
that has not ended. The memory half is `tests/store/test_jobs_contract.py`.
"""

import asyncio
from collections.abc import AsyncIterator

import pytest

from app.store.jobs import Leases
from app.store.pg import PostgresDatabase
from app.store.pg_jobs import JobClaims
from app.worker import run
from tests.integration.conftest import ONE_PROCESS, Provisioned
from tests.jobs_contract import CONTRACT_KINDS, Finish, JobsContract

QUICK = Leases(lease=5.0, renewal=1.0, retry=0.01)
"""A retry that waits a hundredth of a second, so a dead job is dead in a test's time."""

_LEFT = "SELECT count(*) FROM job_claims WHERE state <> 'ended'"


class TestThePostgresSubstrate(JobsContract):
    @pytest.fixture
    async def database(self, provisioned: Provisioned) -> AsyncIterator[PostgresDatabase]:
        postgres = PostgresDatabase(
            dsn=provisioned.app_dsn,
            schema=provisioned.schema,
            draws=ONE_PROCESS,
            kinds=CONTRACT_KINDS,
        )
        await postgres.check()
        try:
            yield postgres
        finally:
            await postgres.close()

    @pytest.fixture
    def finish(self, database: PostgresDatabase) -> Finish:
        claims = JobClaims(database, QUICK)

        async def finished() -> None:
            while True:
                claim = await claims.claim(CONTRACT_KINDS)
                if claim is not None:
                    await run(claims, CONTRACT_KINDS, claim)
                    continue
                async with database.connection() as conn:
                    if await conn.fetchval(_LEFT) == 0:
                        return
                await asyncio.sleep(QUICK.retry)

        return finished
