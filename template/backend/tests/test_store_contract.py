"""One suite, both substrates. The contract `TaskStore` actually promises.

Every test here runs twice -- once against `MemoryDatabase`, once against `PostgresDatabase`
-- and asserts **shapes and behaviour, never seed rows**. The in-memory substrate seeds three
tasks and Postgres starts empty, on purpose: a suite that passes against both cannot have
assumed either, and the moment it does, the two implementations are free to drift.

Ids are the same trap one level down. Memory hands out `"1"`, `"2"`, `"3"`; Postgres hands out
uuids. Nothing below may pattern-match one.
"""

from app.models import CreateTaskBody, UpdateTaskBody
from app.store import Database

NOT_A_TASK_ID = "does-not-exist"
"""Unparsable as a uuid *and* absent from the in-memory list, so one value covers both
substrates -- and it is the value that would reach the driver and come back as a 500."""


async def test_the_substrate_names_itself(database: Database) -> None:
    assert database.name in ("memory", "postgres")


async def test_the_substrate_reports_a_schema_version(database: Database) -> None:
    assert await database.schema_version() is not None


async def test_a_created_task_is_not_done_and_appears_in_the_list(database: Database) -> None:
    store = database.store()
    before = len(await store.list())

    created = await store.create(CreateTaskBody(title="Write the contract suite"))

    assert created.done is False
    assert created.title == "Write the contract suite"
    listed = await store.list()
    assert len(listed) == before + 1
    assert created.id in {task.id for task in listed}


async def test_each_created_task_gets_its_own_id(database: Database) -> None:
    store = database.store()

    first = await store.create(CreateTaskBody(title="one"))
    second = await store.create(CreateTaskBody(title="two"))

    assert first.id != second.id


async def test_the_list_is_in_the_order_they_were_created(database: Database) -> None:
    store = database.store()
    titles = ["first", "second", "third"]

    for title in titles:
        await store.create(CreateTaskBody(title=title))

    listed = await store.list()
    assert [task.title for task in listed[-3:]] == titles


async def test_update_flips_done_and_returns_the_task(database: Database) -> None:
    store = database.store()
    created = await store.create(CreateTaskBody(title="flip me"))

    updated = await store.update(created.id, UpdateTaskBody(done=True))

    assert updated is not None
    assert updated.id == created.id
    assert updated.done is True
    assert {task.id: task.done for task in await store.list()}[created.id] is True


async def test_update_of_an_absent_task_reports_it_rather_than_raising(
    database: Database,
) -> None:
    store = database.store()

    assert await store.update(NOT_A_TASK_ID, UpdateTaskBody(done=True)) is None


async def test_remove_reports_success_and_the_task_is_gone(database: Database) -> None:
    store = database.store()
    created = await store.create(CreateTaskBody(title="remove me"))
    before = len(await store.list())

    assert await store.remove(created.id) is True

    listed = await store.list()
    assert len(listed) == before - 1
    assert created.id not in {task.id for task in listed}


async def test_remove_of_an_absent_task_reports_it_rather_than_raising(
    database: Database,
) -> None:
    store = database.store()

    assert await store.remove(NOT_A_TASK_ID) is False


async def test_removing_twice_reports_failure_the_second_time(database: Database) -> None:
    store = database.store()
    created = await store.create(CreateTaskBody(title="once only"))

    assert await store.remove(created.id) is True
    assert await store.remove(created.id) is False
