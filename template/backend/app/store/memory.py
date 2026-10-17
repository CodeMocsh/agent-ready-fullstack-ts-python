"""`Database` and `TaskStore` in a list — the substrate the hermetic tests run against.

Not a stub with the SQL knocked out. A second implementation of the same contract, which is
what makes the contract a contract: one suite runs against this and against Postgres, and a
rule this cannot keep is a rule the design has not actually specified.

**It resets on restart, and that is the point.** It exists so the demo runs with no
infrastructure and so `make test` needs no database. Do not delete it when Postgres feels
real enough — the fast tier and the frontend's zero-backend mode both stand on it.
"""

from datetime import UTC, datetime

from app.audit_events import AuditEvent, RecordedAuditEvent, audit_event, written
from app.identity.shared import Actor
from app.models import CreateTaskBody, Task, UpdateTaskBody
from app.store import TASKS, AuditEvents, TaskStore, TenantUnset
from app.store.migrate import known_version

SEED: tuple[tuple[str, str, bool], ...] = (
    ("1", "Read AGENTS.md", True),
    ("2", "Run the app in mock mode", False),
    ("3", "Replace this demo with a real feature", False),
)
"""Only this substrate seeds. A real database starts empty, which is why
`tests/store_contract.py` asserts shapes and never seed rows."""


class MemoryTaskStore:
    """`TaskStore` over a list, holding one tenant's rows and its audit events.

    Ids are small integers as strings, unlike Postgres' uuids — deliberate, so that a suite
    passing against both cannot have assumed either. Each method builds its audit event before
    it changes anything, so an event it cannot build leaves the rows as they were.
    """

    def __init__(
        self,
        tenant_id: str,
        tasks: list[Task],
        counter: list[int],
        events: list[RecordedAuditEvent],
    ) -> None:
        self._tenant_id: str = tenant_id
        self._tasks: list[Task] = tasks
        self._counter: list[int] = counter
        self._events: list[RecordedAuditEvent] = events

    def _record(self, event: AuditEvent) -> None:
        self._events.append(
            RecordedAuditEvent.model_validate(
                {
                    **event.model_dump(),
                    "tenant_id": self._tenant_id,
                    "recorded_at": datetime.now(UTC),
                }
            )
        )

    async def list(self, actor: Actor) -> list[Task]:
        tasks = list(self._tasks)
        self._record(audit_event(actor, "read", TASKS, tuple(task.id for task in tasks)))
        return tasks

    async def create(self, actor: Actor, body: CreateTaskBody) -> Task:
        id = str(self._counter[0] + 1)
        event = audit_event(actor, "create", TASKS, (id,), written(body))
        self._counter[0] += 1
        task = Task(id=id, title=body.title, done=False)
        self._tasks.append(task)
        self._record(event)
        return task

    async def update(self, actor: Actor, id: str, body: UpdateTaskBody) -> Task | None:
        event = audit_event(actor, "update", TASKS, (id,), written(body))
        for task in self._tasks:
            if task.id == id:
                task.done = body.done
                self._record(event)
                return task
        return None

    async def remove(self, actor: Actor, id: str) -> bool:
        event = audit_event(actor, "delete", TASKS, (id,))
        for index, task in enumerate(self._tasks):
            if task.id == id:
                del self._tasks[index]
                self._record(event)
                return True
        return False


class MemoryAuditEvents:
    """`AuditEvents` over one tenant's list."""

    def __init__(self, events: list[RecordedAuditEvent]) -> None:
        self._events: list[RecordedAuditEvent] = events

    async def list(self) -> list[RecordedAuditEvent]:
        return list(self._events)


def _refuse_unset(tenant_id: str) -> None:
    if tenant_id.strip() == "":
        raise TenantUnset(
            "the in-memory substrate has no policy to fail closed for it, so an unset "
            "tenant is refused here rather than answered with an empty list"
        )


class MemoryDatabase:
    """One list of rows and one list of audit events per tenant, held for the life of the
    process.

    Exactly one tenant is seeded — the one a deployment with no identity resolver serves. Any
    other starts empty, which is what makes `two tenants do not see each other` a real
    assertion here and not a coincidence of both being handed the same rows. The seed rows
    carry no audit events: nobody read or changed them.
    """

    name: str = "memory"

    def __init__(self, seed_tenant: str) -> None:
        self._tenants: dict[str, list[Task]] = {
            seed_tenant: [Task(id=id, title=title, done=done) for id, title, done in SEED]
        }
        self._events: dict[str, list[RecordedAuditEvent]] = {}
        self._counter: list[int] = [len(SEED)]

    def store(self, tenant_id: str) -> TaskStore:
        _refuse_unset(tenant_id)
        return MemoryTaskStore(
            tenant_id,
            self._tenants.setdefault(tenant_id, []),
            self._counter,
            self._events.setdefault(tenant_id, []),
        )

    def audit_events(self, tenant_id: str) -> AuditEvents:
        _refuse_unset(tenant_id)
        return MemoryAuditEvents(self._events.setdefault(tenant_id, []))

    async def check(self) -> str:
        """A no-op that reports the current version: this substrate has no schema to be
        behind. Reporting the version rather than `None` keeps `Database` uniform, so a
        caller never branches on which substrate it holds."""
        return known_version()

    async def schema_version(self) -> str | None:
        return known_version()

    async def close(self) -> None:
        return None
