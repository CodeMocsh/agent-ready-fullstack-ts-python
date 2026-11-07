"""The jobs contract, against the in-memory substrate, which runs its own jobs.

The Postgres half is `tests/integration/test_jobs_contract.py`, where a worker runs them.
"""

from collections.abc import AsyncIterator

import pytest

from app.identity.shared import SENTINEL_TENANT
from app.store.memory import MemoryDatabase
from tests.jobs_contract import CONTRACT_KINDS, Finish, JobsContract


class TestTheMemorySubstrate(JobsContract):
    @pytest.fixture
    async def database(self) -> AsyncIterator[MemoryDatabase]:
        memory = MemoryDatabase(seed_tenant=SENTINEL_TENANT, kinds=CONTRACT_KINDS)
        yield memory
        await memory.close()

    @pytest.fixture
    def finish(self, database: MemoryDatabase) -> Finish:
        return database.drained
