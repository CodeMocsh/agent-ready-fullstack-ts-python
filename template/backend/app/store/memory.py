"""`Database` and `TaskStore` in a list — the substrate the hermetic tests run against.

Not a stub with the SQL knocked out. A second implementation of the same contract, which is
what makes the contract a contract: one suite runs against this and against Postgres, and a
rule this cannot keep is a rule the design has not actually specified.

**It resets on restart, and that is the point.** It exists so the demo runs with no
infrastructure and so `make test` needs no database. Do not delete it when Postgres feels
real enough — the fast tier and the frontend's zero-backend mode both stand on it.
"""

from app.models import CreateTaskBody, Task, UpdateTaskBody
from app.store import TaskStore, TenantUnset
from app.store.migrate import known_version

SEED: tuple[tuple[str, str, bool], ...] = (
    ("1", "Read AGENTS.md", True),
    ("2", "Run the app in mock mode", False),
    ("3", "Replace this demo with a real feature", False),
)
"""Only this substrate seeds. A real database starts empty, which is why
`tests/test_store_contract.py` asserts shapes and never seed rows."""


class MemoryTaskStore:
    """`TaskStore` over a list, holding one tenant's rows.

    Ids are small integers as strings, unlike Postgres' uuids — deliberate, so that a suite
    passing against both cannot have assumed either.
    """

    def __init__(self, tasks: list[Task], counter: list[int]) -> None:
        self._tasks: list[Task] = tasks
        self._counter: list[int] = counter

    async def list(self) -> list[Task]:
        return list(self._tasks)

    async def create(self, body: CreateTaskBody) -> Task:
        self._counter[0] += 1
        task = Task(id=str(self._counter[0]), title=body.title, done=False)
        self._tasks.append(task)
        return task

    async def update(self, id: str, body: UpdateTaskBody) -> Task | None:
        for task in self._tasks:
            if task.id == id:
                task.done = body.done
                return task
        return None

    async def remove(self, id: str) -> bool:
        for index, task in enumerate(self._tasks):
            if task.id == id:
                del self._tasks[index]
                return True
        return False


class MemoryDatabase:
    """One list per tenant, held for the life of the process.

    Exactly one tenant is seeded — the one a deployment with no identity resolver serves. Any
    other starts empty, which is what makes `two tenants do not see each other` a real
    assertion here and not a coincidence of both being handed the same rows.
    """

    name: str = "memory"

    def __init__(self, seed_tenant: str) -> None:
        self._tenants: dict[str, list[Task]] = {
            seed_tenant: [Task(id=id, title=title, done=done) for id, title, done in SEED]
        }
        self._counter: list[int] = [len(SEED)]

    def store(self, tenant_id: str) -> TaskStore:
        if tenant_id.strip() == "":
            raise TenantUnset(
                "the in-memory substrate has no policy to fail closed for it, so an unset "
                "tenant is refused here rather than answered with an empty list"
            )
        return MemoryTaskStore(self._tenants.setdefault(tenant_id, []), self._counter)

    async def check(self) -> str:
        """A no-op that reports the current version: this substrate has no schema to be
        behind. Reporting the version rather than `None` keeps `Database` uniform, so a
        caller never branches on which substrate it holds."""
        return known_version()

    async def schema_version(self) -> str | None:
        return known_version()

    async def close(self) -> None:
        return None
