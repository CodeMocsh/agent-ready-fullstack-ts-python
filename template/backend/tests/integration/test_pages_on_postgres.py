"""A page is picked in SQL. No read of a list fetches more than one row past the page, so a read
costs what it answers, whatever the tenant holds. `docs/adr/template/0026`."""

from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest

from app.audit_events import acting_as
from app.models import PAGE_MAX, CreateTaskBody
from app.store import Database
from app.store.pg import PostgresDatabase
from tests.pages import every_event, every_task
from tests.store_contract import A_REQUEST, ACTOR

Scoped = Callable[[PostgresDatabase, str], AbstractAsyncContextManager[Any]]


class Counted:
    """A connection that adds the number of rows each `fetch` returns to `fetched`."""

    def __init__(self, conn: Any, fetched: list[int]) -> None:
        self._conn: Any = conn
        self._fetched: list[int] = fetched

    async def fetch(self, *args: Any) -> list[Any]:
        rows: list[Any] = await self._conn.fetch(*args)
        self._fetched.append(len(rows))
        return rows

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


@pytest.fixture
def fetched(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The number of rows each `fetch` on a scoped connection returned, from now on."""
    counts: list[int] = []
    unmeasured: Scoped = PostgresDatabase.scoped

    @asynccontextmanager
    async def counted(self: PostgresDatabase, tenant_id: str) -> AsyncGenerator[Any]:
        async with unmeasured(self, tenant_id) as conn:
            yield Counted(conn, counts)

    monkeypatch.setattr(PostgresDatabase, "scoped", counted)
    return counts


@pytest.fixture(autouse=True)
async def acting() -> AsyncIterator[None]:
    with acting_as(ACTOR):
        yield


async def test_no_read_of_tasks_fetches_more_than_one_row_past_the_page(
    database: Database, fetched: list[int]
) -> None:
    store = database.store(f"paged-{uuid4().hex}")
    for n in range(2 * PAGE_MAX + 1):
        await store.create(CreateTaskBody(title=f"{n}"))

    listed = await every_task(store)

    assert len(listed) == 2 * PAGE_MAX + 1
    assert fetched == [PAGE_MAX + 1, PAGE_MAX + 1, 1]


async def test_no_read_of_audit_events_fetches_more_than_one_row_past_the_page(
    database: Database, fetched: list[int]
) -> None:
    events = database.audit_events(f"paged-{uuid4().hex}")
    for status in range(200, 201 + 2 * PAGE_MAX):
        await events.record(A_REQUEST.model_copy(update={"status": status}))

    recorded = await every_event(events)

    assert len(recorded) == 2 * PAGE_MAX + 1
    assert fetched == [PAGE_MAX + 1, PAGE_MAX + 1, 1]
