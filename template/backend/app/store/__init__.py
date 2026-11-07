"""Persistence: the two seams, and the substrates behind them.

Two levels, deliberately. `Database` is what a process holds before it knows anything about a
request — the schema, the pool, the lifecycle. `TaskStore` is the surface the routes call, and
`Database.store(tenant_id)` is the only way to obtain one.

**A store you can hold is a store already scoped.** The tenant is fixed when the store is
made and is a parameter on no method, so "every method remembered the tenant" is a shape
rather than a habit — which matters because it is exactly the claim nobody can verify by
reading. Postgres enforces it underneath with a row-level security policy; the in-memory
substrate has no policy to lean on, so its scoping is a promise and it fails closed loudly.

**No method takes the actor, and none records an audit event.** On Postgres a trigger records
every write, under the actor `app.audit_events` says is acting, and refuses a write when none
is. `Database.audit_events(tenant_id)` records a request event and reads both kinds back.
`docs/adr/template/0017`.

Two implementations, one contract suite. `tests/store_contract.py` holds the tests and each
substrate runs all of them -- memory in `tests/store/test_store_contract.py`, Postgres in
`tests/integration/test_store_contract.py` -- which is what makes this a contract rather than
a claim, and it is the same pattern the repository already runs one layer up:
`frontend/tests/api/contract.test.ts` against the mock handlers and against this service.
`docs/adr/template/0001` holds that reasoning and the options it rejected.

`app.store.pg` is **not** re-exported here. Importing it is what pulls in the driver, and the
in-memory substrate and every hermetic test run without one. Reach for it as
`from app.store.pg import PostgresDatabase` where a deployment actually needs it — `wiring.py`
is the only module that does.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

from app.audit_events import AuditEventPage, RequestEvent
from app.models import CreateTaskBody, Job, JsonObject, Task, TaskPage, UpdateTaskBody

BIGINT_DIGITS: Final = 19
"""The most digits a `bigint` has."""

BIGINT_MAX: Final = 2**63 - 1
"""The largest `bigint`."""


class TenantUnset(ValueError):
    """A store was asked for without a tenant to scope it to.

    Raised at construction rather than answered emptily at query time. Under the policy an
    unset tenant matches no rows, and no rows is exactly what a true answer looks like — so
    the in-memory substrate, which has no policy, must refuse rather than agree.
    """


class DatabaseBusy(Exception):
    """No connection came to a request in time: every one in the pool stayed in use, or the
    server refused a new one. The request answers the `503` of `NoConnectionFree`, whose
    `Retry-After` is `retry_after`, in seconds: `app/main.py` sends it when a route raises this,
    and `app/request_events.py` when the request event does. `docs/adr/template/0022`."""

    def __init__(self, message: str, *, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after: int = retry_after


@runtime_checkable
class TaskStore(Protocol):
    """Exactly what the routes call, and nothing speculative.

    `list`, `update` and `remove` report a missing task by return value rather than by raising,
    and that is not the droppable-return-value that `AGENTS.md` bans. Run it through the test:
    the design plans for the condition, the contract names it — `404` with a model, in
    `openapi.json` — and the route reports it. Three out of three, so it stays.
    """

    async def list(self, after: str | None) -> TaskPage | None:
        """At most `PAGE_MAX` tasks, newest first: the newest ones when `after` is `None`, else
        the ones created before the task `after` names. `next` names the last task on the page
        when more follow it, and is `None` on the last page. `None` when `after` names no task of
        this tenant."""
        ...

    async def create(self, body: CreateTaskBody) -> Task: ...

    async def update(self, id: str, body: UpdateTaskBody) -> Task | None: ...

    async def remove(self, id: str) -> bool: ...


@runtime_checkable
class AuditEvents(Protocol):
    """One tenant's audit events. Nothing updates or deletes one, and reading them records no
    event."""

    async def record(self, event: RequestEvent) -> None:
        """Record one request event in this tenant."""
        ...

    async def list(self, after: str | None) -> AuditEventPage:
        """At most `PAGE_MAX` events, in the order they were recorded: the first ones when
        `after` is `None`, else the ones after the event `after` names. `after` is the `next` of
        an earlier page. Raises `NoSuchAuditEvent` when `after` names no event of this tenant."""
        ...


@runtime_checkable
class Jobs(Protocol):
    """One tenant's jobs. `docs/adr/template/0027`."""

    async def ask(self, kind: str, input: JsonObject) -> Job:
        """Write a job of `kind` on `input`, asked by the actor that acts here, and return it
        `pending`. Raises `UnknownKind` when no handler runs `kind`, and `AskedByNobody` when no
        actor acts here."""
        ...

    async def read(self, id: str) -> Job | None:
        """The job `id` of this tenant, or `None` when this tenant has no such job."""
        ...


class NoSuchAuditEvent(LookupError):
    """`AuditEvents.list` was handed an `after` that names no event of its tenant: not the
    `next` of one of its pages."""


def numbered(after: str) -> int | None:
    """The number that an audit event's `after` carries, or `None` when `after` is not one:
    ASCII digits for a number from 1 to `BIGINT_MAX`. Both substrates read a cursor with it."""
    if not (after.isascii() and after.isdecimal() and len(after) <= BIGINT_DIGITS):
        return None
    number = int(after)
    return number if 0 < number <= BIGINT_MAX else None


@dataclass(frozen=True)
class Connections:
    """A pool's connections at one moment: lent to a request, waiting in the pool, and the most
    it will open. `pool` names the pool, and is never an address or a credential."""

    pool: str
    used: int
    idle: int
    max_size: int


@runtime_checkable
class Pooled(Protocol):
    """A substrate that holds a pool, and so has connections to count and waits to time."""

    def connections(self) -> Connections: ...

    def time_waits(self, record: Callable[[float], None]) -> None:
        """From now on, hand `record` how long each wait for a connection took, in seconds: a
        wait that ended in a connection, or in no connection within the bound."""
        ...


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

    def audit_events(self, tenant_id: str) -> AuditEvents:
        """One tenant's audit events. Raises `TenantUnset` on an empty tenant, on every
        substrate."""
        ...

    def jobs(self, tenant_id: str) -> Jobs:
        """One tenant's jobs. Raises `TenantUnset` on an empty tenant, on every substrate."""
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
