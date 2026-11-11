"""The jobs contract, against the in-memory substrate, which runs its own jobs.

The Postgres half is `tests/integration/test_jobs_contract.py`, where a worker runs them.
"""

from collections.abc import AsyncIterator

import pytest

from app.audit_events import acting_as
from app.identity.shared import SENTINEL_TENANT
from app.store.memory import MemoryDatabase
from tests.jobs_contract import ASKER, Finish, JobsContract, contract_kinds


class TestTheMemorySubstrate(JobsContract):
    @pytest.fixture
    async def database(self) -> AsyncIterator[MemoryDatabase]:
        memory = MemoryDatabase(seed_tenant=SENTINEL_TENANT, kinds=contract_kinds)
        yield memory
        await memory.close()

    @pytest.fixture
    def finish(self, database: MemoryDatabase) -> Finish:
        return database.drained


async def test_a_job_cancelled_before_it_starts_takes_no_ask_once_per_its_key() -> None:
    memory = MemoryDatabase(seed_tenant=SENTINEL_TENANT, kinds=contract_kinds)
    jobs = memory.jobs(SENTINEL_TENANT)
    with acting_as(ASKER):
        cancelled = await jobs.ask("answers who asked", {"n": 1}, once_per="cancelled")
        await memory.close()
        later = await jobs.ask("answers who asked", {"n": 1}, once_per="cancelled")
        await memory.drained()

    read = await jobs.read(later.id)
    assert read is not None
    assert (later.id != cancelled.id, read.state) == (True, "done")
