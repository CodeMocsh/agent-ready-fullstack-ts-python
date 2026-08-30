"""Persistence: the two seams, and the substrates behind them.

Two levels, deliberately. `Database` is what a process holds before it knows anything about a
request — the schema, the pool, the lifecycle. `TaskStore` is the surface the routes call, and
`Database.store(tenant_id)` is the only way to obtain one.

**A store you can hold is a store already scoped.** The tenant is fixed when the store is
made and is a parameter on no method, so "every method remembered the tenant" is a shape
rather than a habit — which matters because it is exactly the claim nobody can verify by
reading. Postgres enforces it underneath with a row-level security policy; the in-memory
substrate has no policy to lean on, so its scoping is a promise and it fails closed loudly.

Two implementations, one contract suite. `tests/store_contract.py` holds the tests and each
substrate runs all of them -- memory in `tests/store/test_store_contract.py`, Postgres in
`tests/integration/test_store_contract.py` -- which is what makes this a contract rather than
a claim, and it is the same pattern the repository already runs one layer up:
`frontend/tests/api/contract.test.ts` against the mock handlers and against this service.
`docs/adr/0001` holds that reasoning and the options it rejected.

`app.store.pg` is **not** re-exported here. Importing it is what pulls in the driver, and the
in-memory substrate and every hermetic test run without one. Reach for it as
`from app.store.pg import PostgresDatabase` where a deployment actually needs it — `wiring.py`
is the only module that does.
"""

from typing import Protocol, runtime_checkable

from app.models import CreateTaskBody, Task, UpdateTaskBody


class TenantUnset(ValueError):
    """A store was asked for without a tenant to scope it to.

    Raised at construction rather than answered emptily at query time. Under the policy an
    unset tenant matches no rows, and no rows is exactly what a true answer looks like — so
    the in-memory substrate, which has no policy, must refuse rather than agree.
    """


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

    def store(self, tenant_id: str) -> TaskStore:
        """The routes' surface, scoped to one tenant. Cheap — the connection is shared.

        Raises `TenantUnset` on an empty tenant, on every substrate.
        """
        ...

    async def check(self) -> str:
        """Verify the schema is the one this build expects. Returns the last entry key.

        Never applies anything. This process holds no rights to, by design: DDL is a release
        step (`make migrate`), and the refusal here is what makes a release that skipped it
        fail loudly at startup instead of quietly at the first query.
        """
        ...

    async def schema_version(self) -> str | None:
        """The last entry key applied, or `None` on a substrate that was never migrated."""
        ...

    async def close(self) -> None: ...
