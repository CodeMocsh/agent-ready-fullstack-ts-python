"""The store contract, against the in-memory substrate. Hermetic, so it is in the fast tier.

The Postgres half of the same contract is `tests/integration/test_store_contract.py`, which needs a
server and therefore lives in the on-demand suite. Both subclass the same class, so neither
substrate can quietly keep a different promise.
"""

from collections.abc import AsyncIterator

import pytest

from app.identity import SENTINEL_TENANT
from app.store import Database
from app.store.memory import MemoryDatabase
from tests.store_contract import TaskStoreContract


class TestTheMemorySubstrate(TaskStoreContract):
    substrate: str = "memory"

    @pytest.fixture
    async def database(self) -> AsyncIterator[Database]:
        memory = MemoryDatabase(seed_tenant=SENTINEL_TENANT)
        yield memory
        await memory.close()
