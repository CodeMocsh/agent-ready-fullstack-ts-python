"""Every row of a list, read a page at a time: what a test reads to see a whole list. A route
reads one page and never this."""

from app.audit_events import RecordedAuditEvent
from app.models import Task
from app.store import AuditEvents, TaskStore


async def every_task(store: TaskStore) -> list[Task]:
    """Each task of the store's tenant, newest first."""
    tasks: list[Task] = []
    after: str | None = None
    while True:
        page = await store.list(after)
        assert page is not None, f"the task {after} that a page named as its next is gone"
        tasks += page.tasks
        if page.next is None:
            return tasks
        after = page.next


async def every_event(events: AuditEvents) -> list[RecordedAuditEvent]:
    """Each audit event of the tenant, in the order they were recorded."""
    recorded: list[RecordedAuditEvent] = []
    after: str | None = None
    while True:
        page = await events.list(after)
        recorded += page.events
        if page.next is None:
            return recorded
        after = page.next
