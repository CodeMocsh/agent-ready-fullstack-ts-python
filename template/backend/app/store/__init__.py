"""Persistence: the two seams, and the substrates behind them.

Two levels, deliberately. `Database` is what a process holds before it knows anything about a
request — the schema, the pool, the lifecycle. `TaskStore` is the surface the routes call, and
`Database.store()` is the only way to obtain one. Today that split buys uniform lifecycle
handling; the reason it is worth having now is that request scoping — a tenant, a user, an
organisation — is a parameter on `store()` when it arrives, rather than an audit of every
method that was supposed to remember it.

Two implementations, one contract suite. `tests/test_store_contract.py` runs the same suite
against both, which is what makes this a contract rather than a claim, and it is the same
pattern the repository already runs one layer up: `frontend/tests/contract.test.ts` against
the mock handlers and against this service.

`app.store.pg` is **not** re-exported here. Importing it is what pulls in the driver, and the
in-memory substrate and every hermetic test run without one. Reach for it as
`from app.store.pg import PostgresDatabase` where a deployment actually needs it — `wiring.py`
is the only module that does.
"""

from typing import Protocol, runtime_checkable

from app.models import CreateTaskBody, Task, UpdateTaskBody


@runtime_checkable
class TaskStore(Protocol):
    """Exactly what the routes call, and nothing speculative.

    `update` and `remove` report a missing task by return value rather than by raising, and
    that is not the droppable-return-value that `AGENTS.md` bans. Run it through the test:
    the design plans for the condition, the contract names it — `404` with a model, in
    `openapi.json` — and the route reports it. Three out of three, so it stays.
    """

    async def list(self) -> list[Task]: ...

    async def create(self, body: CreateTaskBody) -> Task: ...

    async def update(self, id: str, body: UpdateTaskBody) -> Task | None: ...

    async def remove(self, id: str) -> bool: ...


@runtime_checkable
class Database(Protocol):
    """What a process holds before any request. One per process, on the app's lifespan."""

    name: str
    """Which substrate this is. A fact rather than a guess: a deployment that silently came
    up on the in-memory substrate has data that will not be there tomorrow, and the class
    name of the object is not something an implementation outside this repo has to match."""

    def store(self) -> TaskStore:
        """The routes' surface. Cheap — implementations share the connection behind it."""
        ...

    async def migrate(self) -> str:
        """Bring the schema up, or verify that someone has. Returns the version marker."""
        ...

    async def schema_version(self) -> str | None:
        """The applied version marker, or `None` on a substrate that was never migrated."""
        ...

    async def close(self) -> None: ...
