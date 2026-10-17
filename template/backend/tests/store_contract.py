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

The audit-event tests read events back through `Database.audit_events`, and assert only the
events their own operations recorded: a substrate may hold events from before the test.

The tests named for two tenants, or for a store without one, are the tenancy contract. Postgres
keeps it with a forced policy and memory keeps it with a dictionary, and the suite does not care
which -- only that neither can be talked into answering for a tenant it was not scoped to.
"""

import pytest

from app.audit_events import UnrecordableActor
from app.identity.shared import SENTINEL_TENANT, Actor
from app.models import CreateTaskBody, UpdateTaskBody
from app.store import TASKS, Database, TenantUnset

OTHER_TENANT = "a-second-tenant"

ACTOR = Actor(issuer="a-test", subject="the-contract", name="The contract suite")
"""Who every operation below acts as."""

PERSON = Actor(issuer="a-test", subject="a-person", name="A person")
AGENT = Actor(issuer="tasks", subject="an-agent", name="An agent", on_behalf_of=PERSON)
NESTED = Actor(issuer="tasks", subject="a-sub-agent", name="A sub-agent", on_behalf_of=AGENT)
"""An actor two levels deep, which no substrate can record."""

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
        before = len(await store.list(ACTOR))

        created = await store.create(ACTOR, CreateTaskBody(title="Write the contract suite"))

        assert created.done is False
        assert created.title == "Write the contract suite"
        listed = await store.list(ACTOR)
        assert len(listed) == before + 1
        assert created.id in {task.id for task in listed}

    async def test_each_created_task_gets_its_own_id(self, database: Database) -> None:
        store = database.store(SENTINEL_TENANT)

        first = await store.create(ACTOR, CreateTaskBody(title="one"))
        second = await store.create(ACTOR, CreateTaskBody(title="two"))

        assert first.id != second.id

    async def test_the_list_is_in_the_order_they_were_created(self, database: Database) -> None:
        store = database.store(SENTINEL_TENANT)
        titles = ["first", "second", "third"]

        for title in titles:
            await store.create(ACTOR, CreateTaskBody(title=title))

        listed = await store.list(ACTOR)
        assert [task.title for task in listed[-3:]] == titles

    async def test_update_flips_done_and_returns_the_task(self, database: Database) -> None:
        store = database.store(SENTINEL_TENANT)
        created = await store.create(ACTOR, CreateTaskBody(title="flip me"))

        updated = await store.update(ACTOR, created.id, UpdateTaskBody(done=True))

        assert updated is not None
        assert updated.id == created.id
        assert updated.done is True
        assert {task.id: task.done for task in await store.list(ACTOR)}[created.id] is True

    async def test_update_of_an_absent_task_reports_it_rather_than_raising(
        self, database: Database
    ) -> None:
        store = database.store(SENTINEL_TENANT)

        assert await store.update(ACTOR, NOT_A_TASK_ID, UpdateTaskBody(done=True)) is None

    async def test_remove_reports_success_and_the_task_is_gone(self, database: Database) -> None:
        store = database.store(SENTINEL_TENANT)
        created = await store.create(ACTOR, CreateTaskBody(title="remove me"))
        before = len(await store.list(ACTOR))

        assert await store.remove(ACTOR, created.id) is True

        listed = await store.list(ACTOR)
        assert len(listed) == before - 1
        assert created.id not in {task.id for task in listed}

    async def test_remove_of_an_absent_task_reports_it_rather_than_raising(
        self, database: Database
    ) -> None:
        store = database.store(SENTINEL_TENANT)

        assert await store.remove(ACTOR, NOT_A_TASK_ID) is False

    async def test_removing_twice_reports_failure_the_second_time(self, database: Database) -> None:
        store = database.store(SENTINEL_TENANT)
        created = await store.create(ACTOR, CreateTaskBody(title="once only"))

        assert await store.remove(ACTOR, created.id) is True
        assert await store.remove(ACTOR, created.id) is False

    async def test_two_tenants_do_not_see_each_others_tasks(self, database: Database) -> None:
        mine = database.store(SENTINEL_TENANT)
        theirs = database.store(OTHER_TENANT)
        before = len(await theirs.list(ACTOR))

        created = await mine.create(ACTOR, CreateTaskBody(title="Not yours"))

        assert created.id not in {task.id for task in await theirs.list(ACTOR)}
        assert len(await theirs.list(ACTOR)) == before

    async def test_a_store_cannot_be_had_without_a_tenant(self, database: Database) -> None:
        """On both substrates, and at construction. There is no unscoped store to hold, which
        is what stops a route from forgetting -- and an unset tenant answers emptily under the
        policy, which is indistinguishable from a tenant that owns nothing."""
        for empty in ("", "   "):
            with pytest.raises(TenantUnset):
                database.store(empty)

    async def test_every_operation_records_who_did_what_to_which_task(
        self, database: Database
    ) -> None:
        store = database.store(SENTINEL_TENANT)
        events = database.audit_events(SENTINEL_TENANT)
        before = len(await events.list())

        created = await store.create(ACTOR, CreateTaskBody(title="audit me"))
        await store.update(ACTOR, created.id, UpdateTaskBody(done=True))
        listed = await store.list(ACTOR)
        await store.remove(ACTOR, created.id)

        recorded = (await events.list())[before:]
        assert [(e.action, e.resource_type, e.resource_ids, e.fields) for e in recorded] == [
            ("create", TASKS, (created.id,), ("title",)),
            ("update", TASKS, (created.id,), ("done",)),
            ("read", TASKS, tuple(task.id for task in listed), ()),
            ("delete", TASKS, (created.id,), ()),
        ]
        assert [event.actor for event in recorded] == [ACTOR] * len(recorded)
        assert [event.tenant_id for event in recorded] == [SENTINEL_TENANT] * len(recorded)

    async def test_an_event_holds_no_value_a_row_holds(self, database: Database) -> None:
        store = database.store(SENTINEL_TENANT)
        events = database.audit_events(SENTINEL_TENANT)
        before = len(await events.list())

        await store.create(ACTOR, CreateTaskBody(title="a value only the row may hold"))

        assert "a value only the row may hold" not in repr((await events.list())[before:])

    async def test_an_operation_on_an_absent_task_records_nothing(self, database: Database) -> None:
        """It touched no row, so there is no row to name."""
        store = database.store(SENTINEL_TENANT)
        events = database.audit_events(SENTINEL_TENANT)
        before = len(await events.list())

        await store.update(ACTOR, NOT_A_TASK_ID, UpdateTaskBody(done=True))
        await store.remove(ACTOR, NOT_A_TASK_ID)

        assert len(await events.list()) == before

    async def test_an_app_acting_for_a_person_is_recorded_with_the_person(
        self, database: Database
    ) -> None:
        store = database.store(SENTINEL_TENANT)
        events = database.audit_events(SENTINEL_TENANT)

        await store.create(AGENT, CreateTaskBody(title="drafted by an agent"))

        assert (await events.list())[-1].actor == AGENT

    async def test_an_actor_that_cannot_be_recorded_changes_nothing(
        self, database: Database
    ) -> None:
        store = database.store(SENTINEL_TENANT)
        before = len(await store.list(ACTOR))

        with pytest.raises(UnrecordableActor):
            await store.create(NESTED, CreateTaskBody(title="never stored"))

        assert len(await store.list(ACTOR)) == before

    async def test_an_actor_that_cannot_be_recorded_is_refused_whatever_the_rows(
        self, database: Database
    ) -> None:
        """Whether the task is there, or the list is empty, decides nothing: the actor is
        refused on every substrate."""
        store = database.store(SENTINEL_TENANT)

        with pytest.raises(UnrecordableActor):
            await store.list(NESTED)
        with pytest.raises(UnrecordableActor):
            await store.update(NESTED, NOT_A_TASK_ID, UpdateTaskBody(done=True))
        with pytest.raises(UnrecordableActor):
            await store.remove(NESTED, NOT_A_TASK_ID)

    async def test_an_operation_outside_a_request_names_no_request(
        self, database: Database
    ) -> None:
        store = database.store(SENTINEL_TENANT)
        events = database.audit_events(SENTINEL_TENANT)

        await store.create(ACTOR, CreateTaskBody(title="from a job"))

        recorded = (await events.list())[-1]
        assert (recorded.request_id, recorded.trace_id) == (None, None)

    async def test_two_tenants_do_not_see_each_others_audit_events(
        self, database: Database
    ) -> None:
        theirs = database.audit_events(OTHER_TENANT)
        before = len(await theirs.list())

        await database.store(SENTINEL_TENANT).create(ACTOR, CreateTaskBody(title="Not yours"))

        assert len(await theirs.list()) == before

    async def test_audit_events_cannot_be_had_without_a_tenant(self, database: Database) -> None:
        for empty in ("", "   "):
            with pytest.raises(TenantUnset):
                database.audit_events(empty)
