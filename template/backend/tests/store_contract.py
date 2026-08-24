"""One contract, written once, run by every substrate that claims to keep it.

`TaskStoreContract` is not collected: it is not in a `test_*.py` file and its name is not
`Test*`. A substrate runs it by subclassing it and supplying a `database` fixture --
`tests/store/test_store_contract.py` for memory, `tests/integration/test_store_contract.py`
for Postgres. Each subclass is a suite of its own, so a substrate that is not there today is
a suite that is not in the run, never a test that skips itself.

Every test here asserts **shapes and behaviour, never seed rows**. The in-memory substrate
seeds three tasks and Postgres starts empty, on purpose: a suite that passes against both
cannot have assumed either, and the moment it does, the two implementations are free to
drift.

Ids are the same trap one level down. Memory hands out `"1"`, `"2"`, `"3"`; Postgres hands
out uuids. Nothing below may pattern-match one.

The last two tests are the tenancy contract. Postgres keeps it with a forced policy and
memory keeps it with a dictionary, and the suite does not care which -- only that neither can
be talked into answering for a tenant it was not scoped to.
"""

import pytest

from app.identity import SENTINEL_TENANT
from app.models import CreateTaskBody, UpdateTaskBody
from app.store import Database, TenantUnset

OTHER_TENANT = "a-second-tenant"

NOT_A_TASK_ID = "does-not-exist"
"""Unparsable as a uuid *and* absent from the in-memory list, so one value covers both
substrates -- and it is the value that would reach the driver and come back as a 500."""


class TaskStoreContract:
    """What `TaskStore` promises. Subclass it, set `substrate`, and give it a `database`."""

    substrate: str = ""
    """The name the substrate under test reports. Each subclass names its own, and the first
    test below fails on a subclass that forgot to."""

    async def test_the_substrate_names_itself(self, database: Database) -> None:
        assert database.name == self.substrate

    async def test_the_substrate_reports_a_schema_version(self, database: Database) -> None:
        assert await database.schema_version() is not None

    async def test_a_created_task_is_not_done_and_appears_in_the_list(
        self, database: Database
    ) -> None:
        store = database.store(SENTINEL_TENANT)
        before = len(await store.list())

        created = await store.create(CreateTaskBody(title="Write the contract suite"))

        assert created.done is False
        assert created.title == "Write the contract suite"
        listed = await store.list()
        assert len(listed) == before + 1
        assert created.id in {task.id for task in listed}

    async def test_each_created_task_gets_its_own_id(self, database: Database) -> None:
        store = database.store(SENTINEL_TENANT)

        first = await store.create(CreateTaskBody(title="one"))
        second = await store.create(CreateTaskBody(title="two"))

        assert first.id != second.id

    async def test_the_list_is_in_the_order_they_were_created(self, database: Database) -> None:
        store = database.store(SENTINEL_TENANT)
        titles = ["first", "second", "third"]

        for title in titles:
            await store.create(CreateTaskBody(title=title))

        listed = await store.list()
        assert [task.title for task in listed[-3:]] == titles

    async def test_update_flips_done_and_returns_the_task(self, database: Database) -> None:
        store = database.store(SENTINEL_TENANT)
        created = await store.create(CreateTaskBody(title="flip me"))

        updated = await store.update(created.id, UpdateTaskBody(done=True))

        assert updated is not None
        assert updated.id == created.id
        assert updated.done is True
        assert {task.id: task.done for task in await store.list()}[created.id] is True

    async def test_update_of_an_absent_task_reports_it_rather_than_raising(
        self, database: Database
    ) -> None:
        store = database.store(SENTINEL_TENANT)

        assert await store.update(NOT_A_TASK_ID, UpdateTaskBody(done=True)) is None

    async def test_remove_reports_success_and_the_task_is_gone(self, database: Database) -> None:
        store = database.store(SENTINEL_TENANT)
        created = await store.create(CreateTaskBody(title="remove me"))
        before = len(await store.list())

        assert await store.remove(created.id) is True

        listed = await store.list()
        assert len(listed) == before - 1
        assert created.id not in {task.id for task in listed}

    async def test_remove_of_an_absent_task_reports_it_rather_than_raising(
        self, database: Database
    ) -> None:
        store = database.store(SENTINEL_TENANT)

        assert await store.remove(NOT_A_TASK_ID) is False

    async def test_removing_twice_reports_failure_the_second_time(self, database: Database) -> None:
        store = database.store(SENTINEL_TENANT)
        created = await store.create(CreateTaskBody(title="once only"))

        assert await store.remove(created.id) is True
        assert await store.remove(created.id) is False

    async def test_two_tenants_do_not_see_each_others_tasks(self, database: Database) -> None:
        mine = database.store(SENTINEL_TENANT)
        theirs = database.store(OTHER_TENANT)
        before = len(await theirs.list())

        created = await mine.create(CreateTaskBody(title="Not yours"))

        assert created.id not in {task.id for task in await theirs.list()}
        assert len(await theirs.list()) == before

    async def test_a_store_cannot_be_had_without_a_tenant(self, database: Database) -> None:
        """On both substrates, and at construction. There is no unscoped store to hold, which
        is what stops a route from forgetting -- and an unset tenant answers emptily under the
        policy, which is indistinguishable from a tenant that owns nothing."""
        for empty in ("", "   "):
            with pytest.raises(TenantUnset):
                database.store(empty)
