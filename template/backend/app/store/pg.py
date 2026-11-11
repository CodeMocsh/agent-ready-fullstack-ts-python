"""`Database` and `TaskStore` over asyncpg.

**The search path is a startup parameter, not a statement.** asyncpg runs `RESET ALL` when a
pooled connection is released, and `RESET ALL` *restores* startup parameters while *clearing*
session `SET`s. So a `SET search_path` issued from a connect hook survives exactly one
checkout and every checkout after it resolves against `public` — silently, because the first
query on each connection works, which is what makes it look like an intermittent bug in
whatever ran second. `resolve_schema` has already refused anything that is not a bare
lowercase identifier, so the name cannot carry anything the startup packet would misread.

**The tenant is `SET LOCAL`, never `SET`.** A session-scoped setting outlives the transaction
that made it, and the next request handed that pooled connection inherits it — one tenant
reading another's rows, with nothing in any log to say so. `set_config(..., true)` is the
local form and every statement below runs inside a transaction that has issued it.

**The actor is set on every connection the pool lends.** The pool's `setup` sets every setting `audit_row_change`
reads, from `audit_events.current_actor()` and the request's ids, before it lends the
connection. They are session settings, and the pool sets all of them each time it lends a
connection. A connection lent again does not carry the actor it had.

**The pool is sized from the connection budget.** Before it opens, the pool reads the server's
limits on one connection of its own, and takes what remains of the process's share. A pool that
would get no connection refuses to start. A request that gets no connection, or whose connection
ends, raises `DatabaseUnavailable`, which `app/main.py` answers with a `503`. `docs/adr/template/0022`.

**A connection that ends while it is lent is `ConnectionLost`.** asyncpg names the end in several
ways: `ConnectionDoesNotExistError` during a statement, and an `InterfaceError` that says the
connection went back to the pool once it ended between statements. So the pool reads whether the
connection ended, and not the type of the error.

**Every wait has a bound.** A statement, an answer, a transaction left idle, and a wait for a
connection each raise once `Timeouts` says so. `docs/deployment.md` says why a platform's
request timeout is not one of them. The bounds on the statement and the idle transaction are
startup parameters, for the reason the search path is one.

**No environment variable is read here.** The DSN and the schema are constructor arguments and
`wiring.py` is the only reader, which is what lets one process hold two of these at once —
what the contract suite does.
"""

import asyncio
import json
import math
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID, uuid4

from app import log
from app.audit_events import AuditEventPage, RecordedAuditEvent, RequestEvent, current_actor
from app.identity.shared import Actor
from app.models import CreateTaskBody, Job, JsonObject, Task, TaskPage, UpdateTaskBody
from app.store import (
    AuditEvents,
    Connections,
    DatabaseUnavailable,
    Jobs,
    NoSuchAuditEvent,
    TaskStore,
    TenantUnset,
    numbered,
)
from app.store.acting import SET_ACTING, acting
from app.store.budget import ConnectionBudget, Draws, budgeted, server_limits
from app.store.conn import resolve_schema
from app.store.ddl import TENANT_GUC
from app.store.jobs import Kinds, LeaseLost, asker, encoded, fence, handler_of
from app.store.migrate import check, schema_version
from app.store.pages import fetch_page

_SET_TENANT = "SELECT set_config($1, $2, true)"
_TASKS = "SELECT id, title, done, seq FROM tasks"
_TASKS_BEFORE = "SELECT id, title, done, seq FROM tasks WHERE seq < $1"
_SEQ_OF_TASK = "SELECT seq FROM tasks WHERE id = $1"
_CREATE = "INSERT INTO tasks (id, tenant_id, title) VALUES ($1, $2, $3) RETURNING id, title, done"
_UPDATE = "UPDATE tasks SET done = $2 WHERE id = $1 RETURNING id, title, done"
_REMOVE = "DELETE FROM tasks WHERE id = $1 RETURNING id"
_RECORD = (
    "INSERT INTO audit_events (tenant_id, actor_issuer, actor_subject, actor_name, "
    "on_behalf_of_issuer, on_behalf_of_subject, on_behalf_of_name, method, route, status, "
    "resource_ids, fields, request_id, trace_id) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, '{}', $12, $13)"
)
_AUDIT_EVENTS = (
    "SELECT seq, tenant_id, recorded_at, actor_issuer, actor_subject, actor_name, "
    "on_behalf_of_issuer, on_behalf_of_subject, on_behalf_of_name, action, outcome, "
    "resource_type, method, route, status, resource_ids, fields, request_id, trace_id "
    "FROM audit_events WHERE seq > $1"
)
_AN_AUDIT_EVENT = "SELECT EXISTS (SELECT 1 FROM audit_events WHERE seq = $1)"
_ASK_CLAIM = "INSERT INTO job_claims (id, tenant_id, kind) VALUES ($1, $2, $3)"
_ASK_JOB = (
    "INSERT INTO jobs (id, tenant_id, kind, input, actor_issuer, actor_subject, actor_name, "
    "on_behalf_of_issuer, on_behalf_of_subject, on_behalf_of_name) "
    "VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8, $9, $10) "
    "RETURNING id, kind, state, outcome, failure"
)
_WAKE = "SELECT pg_notify($1, '')"
_A_JOB = "SELECT id, kind, state, outcome, failure FROM jobs WHERE id = $1"
_HOLD_CLAIM = (
    "SELECT 1 FROM job_claims WHERE id = $1 AND tenant_id = $2 AND token = $3 "
    "AND state = 'running' AND lease_ends > now() FOR KEY SHARE"
)


SMALLEST_BOUND: Final = 0.001


@dataclass(frozen=True)
class Timeouts:
    """How long each wait may last, in seconds, before it raises. Each is at least a
    millisecond, because Postgres reads a bound of zero as no bound at all."""

    statement: float = 5.0
    """Postgres cancels a statement that runs longer, and asyncpg raises `QueryCanceledError`."""

    idle_in_transaction: float = 10.0
    """Postgres ends a session that holds a transaction open and sends nothing. Its locks go
    with it."""

    acquire: float = 5.0
    """A request that finds every connection in use waits this long, then `AcquireTimedOut`."""

    def __post_init__(self) -> None:
        if min(self.statement, self.idle_in_transaction, self.acquire) < SMALLEST_BOUND:
            raise ValueError(f"{self} holds a bound under a millisecond, which Postgres ignores")

    @property
    def answer(self) -> float:
        """How long asyncpg waits for any answer before it raises `TimeoutError`. A second
        longer than `statement`, so Postgres reports a slow statement first."""
        return self.statement + 1.0


TIMEOUTS: Final = Timeouts()


class AcquireTimedOut(DatabaseUnavailable):
    """No connection came to a request within `Timeouts.acquire`: every one stayed in use, or a
    new one did not open in time."""


class ConnectionRefused(DatabaseUnavailable):
    """A new connection did not open: the server holds as many as it allows, it is starting or
    stopping, or nothing answers at its address."""


class ConnectionLost(DatabaseUnavailable):
    """A lent connection ended during a unit of work: the server stopped or ended the session, or
    the network dropped it. The unit may have committed. A transaction left idle past
    `Timeouts.idle_in_transaction` ends this way too."""


def _milliseconds(seconds: float) -> str:
    return str(round(seconds * 1000))


def _driver() -> Any:
    """asyncpg, declared as the untyped boundary it is and imported where it is needed.

    It ships no annotations, so every `Connection` and `Pool` reached through it is unknown to
    the type checker. Saying `Any` once, here, is what keeps that from becoming a suppression
    at each call site — and `AGENTS.md` bans the spelling of a suppression outright.

    Imported inside the function so that importing this module costs nothing on a process that
    never opens a connection. A project that deletes asyncpg still starts without telemetry;
    with telemetry, `app.telemetry` imports asyncpg through its instrumentation.
    """
    import asyncpg

    return asyncpg


async def _name_who_acts(conn: Any) -> None:
    """Set the actor and the request's ids on a connection the pool is about to lend."""
    await conn.execute(
        SET_ACTING, *acting(current_actor(), log.current_request_id(), log.current_trace_id())
    )


def _refused_by_the_server() -> tuple[type[BaseException], ...]:
    """What a new connection raises when it does not open: the server refuses it, a session ends
    before the pool lends it, or the socket does not connect. A wrong password or database is
    none of these: no retry fixes it."""
    asyncpg = _driver()
    return (
        asyncpg.TooManyConnectionsError,
        asyncpg.CannotConnectNowError,
        asyncpg.AdminShutdownError,
        asyncpg.CrashShutdownError,
        asyncpg.PostgresConnectionError,
        OSError,
    )


def failed_on_a_connection() -> tuple[type[BaseException], ...]:
    """What a statement raises from the driver or the socket. Each is `ConnectionLost` when the
    connection ended with it."""
    asyncpg = _driver()
    return (asyncpg.PostgresError, asyncpg.InterfaceError, OSError)


def _ended_but_still_lent(conn: Any) -> bool:
    """Whether Postgres ended this pooled connection while the pool still lends it. One the pool
    took back is detached, with `_con` set to `None`, and is not."""
    return conn._con is not None and conn.is_closed()


def _ended(conn: Any) -> bool:
    """Whether this lent connection ended. asyncpg detaches a lent connection that ends between
    statements, so a detached one ended too."""
    return conn._con is None or conn.is_closed()


def _task(row: Any) -> Task:
    return Task(id=str(row["id"]), title=row["title"], done=row["done"])


def actor_from(issuer: str | None, subject: str | None, name: str | None) -> Actor | None:
    """The actor the three columns name, `None` when all three are null. Raises
    `pydantic.ValidationError` when only some are."""
    if issuer is None and subject is None and name is None:
        return None
    return Actor.model_validate({"issuer": issuer, "subject": subject, "name": name})


def _recorded(row: Any) -> RecordedAuditEvent:
    acted_for = actor_from(
        row["on_behalf_of_issuer"], row["on_behalf_of_subject"], row["on_behalf_of_name"]
    )
    return RecordedAuditEvent(
        tenant_id=row["tenant_id"],
        recorded_at=row["recorded_at"],
        actor=Actor(
            issuer=row["actor_issuer"],
            subject=row["actor_subject"],
            name=row["actor_name"],
            on_behalf_of=acted_for,
        ),
        action=row["action"],
        outcome=row["outcome"],
        resource_type=row["resource_type"],
        method=row["method"],
        route=row["route"],
        status=row["status"],
        resource_ids=tuple(row["resource_ids"]),
        fields=tuple(row["fields"]),
        request_id=row["request_id"],
        trace_id=row["trace_id"],
    )


def _no_such_event(after: str | None) -> NoSuchAuditEvent:
    return NoSuchAuditEvent(f"no audit event {after!r} in this tenant")


def _as_id(id: str) -> UUID | None:
    """The path parameter as a uuid, or `None` when it is not one.

    `id` arrives from a URL and the column is `uuid`, so an unparsable value would otherwise
    reach the driver and come back as a 500. A task that cannot exist is a 404 — the same
    answer the in-memory substrate gives, which is why the contract suite asserts it.
    """
    try:
        return UUID(id)
    except ValueError:
        return None


class PostgresTaskStore:
    """`TaskStore` against a pool, scoped to one tenant. Constructed per request and cheap.

    Every statement runs inside a transaction that has set the tenant. Nothing here filters by
    `tenant_id` in SQL: the policy does it, which is the point — a `WHERE` clause somebody
    forgets is a promise, and a forced policy is a mechanism.
    """

    def __init__(self, database: "PostgresDatabase", tenant_id: str) -> None:
        self._database: PostgresDatabase = database
        self._tenant_id: str = tenant_id

    async def list(self, after: str | None) -> TaskPage | None:
        async with self._database.scoped(self._tenant_id) as conn:
            if after is None:
                page = await fetch_page(conn, _TASKS, "seq DESC")
            else:
                parsed = _as_id(after)
                seq = None if parsed is None else await conn.fetchval(_SEQ_OF_TASK, parsed)
                if seq is None:
                    return None
                page = await fetch_page(conn, _TASKS_BEFORE, "seq DESC", seq)
        tasks = [_task(row) for row in page.rows]
        return TaskPage(tasks=tasks, next=tasks[-1].id if page.more else None)

    async def create(self, body: CreateTaskBody) -> Task:
        async with self._database.scoped(self._tenant_id) as conn:
            return _task(await conn.fetchrow(_CREATE, uuid4(), self._tenant_id, body.title))

    async def update(self, id: str, body: UpdateTaskBody) -> Task | None:
        parsed = _as_id(id)
        if parsed is None:
            return None
        async with self._database.scoped(self._tenant_id) as conn:
            row = await conn.fetchrow(_UPDATE, parsed, body.done)
            return None if row is None else _task(row)

    async def remove(self, id: str) -> bool:
        parsed = _as_id(id)
        if parsed is None:
            return False
        async with self._database.scoped(self._tenant_id) as conn:
            return await conn.fetchval(_REMOVE, parsed) is not None


class PostgresAuditEvents:
    """`AuditEvents` against a pool, scoped to one tenant by the same policy as its rows."""

    def __init__(self, database: "PostgresDatabase", tenant_id: str) -> None:
        self._database: PostgresDatabase = database
        self._tenant_id: str = tenant_id

    async def record(self, event: RequestEvent) -> None:
        acted_for = event.actor.on_behalf_of
        async with self._database.scoped(self._tenant_id) as conn:
            await conn.execute(
                _RECORD,
                self._tenant_id,
                event.actor.issuer,
                event.actor.subject,
                event.actor.name,
                None if acted_for is None else acted_for.issuer,
                None if acted_for is None else acted_for.subject,
                None if acted_for is None else acted_for.name,
                event.method,
                event.route,
                event.status,
                list(event.resource_ids),
                event.request_id,
                event.trace_id,
            )

    async def list(self, after: str | None) -> AuditEventPage:
        seq = 0 if after is None else numbered(after)
        if seq is None:
            raise _no_such_event(after)
        async with self._database.scoped(self._tenant_id) as conn:
            if after is not None and not await conn.fetchval(_AN_AUDIT_EVENT, seq):
                raise _no_such_event(after)
            page = await fetch_page(conn, _AUDIT_EVENTS, "seq", seq)
        return AuditEventPage(
            events=tuple(_recorded(row) for row in page.rows),
            next=str(page.rows[-1]["seq"]) if page.more else None,
        )


def _job(row: Any) -> Job:
    outcome = row["outcome"]
    return Job(
        id=str(row["id"]),
        kind=row["kind"],
        state=row["state"],
        outcome=None if outcome is None else json.loads(outcome),
        failure=row["failure"],
    )


def wakes(schema: str) -> str:
    """The channel on which a job that is asked for in `schema` wakes the workers."""
    return f"{schema}_jobs"


class PostgresJobs:
    """`Jobs` against a pool, scoped to one tenant. A job is two rows: its claim in `job_claims`,
    which no policy scopes, and the job in `jobs`, which the policy scopes."""

    def __init__(self, database: "PostgresDatabase", tenant_id: str) -> None:
        self._database: PostgresDatabase = database
        self._tenant_id: str = tenant_id

    async def ask(self, kind: str, input: JsonObject) -> Job:
        _ = handler_of(self._database.kinds, kind)
        actor = asker()
        stored = encoded(input)
        acted_for = actor.on_behalf_of
        id = uuid4()
        async with self._database.scoped(self._tenant_id) as conn:
            await conn.execute(_ASK_CLAIM, id, self._tenant_id, kind)
            row = await conn.fetchrow(
                _ASK_JOB,
                id,
                self._tenant_id,
                kind,
                stored,
                actor.issuer,
                actor.subject,
                actor.name,
                None if acted_for is None else acted_for.issuer,
                None if acted_for is None else acted_for.subject,
                None if acted_for is None else acted_for.name,
            )
            await conn.execute(_WAKE, wakes(self._database.schema))
        return _job(row)

    async def read(self, id: str) -> Job | None:
        parsed = _as_id(id)
        if parsed is None:
            return None
        async with self._database.scoped(self._tenant_id) as conn:
            row = await conn.fetchrow(_A_JOB, parsed)
        return None if row is None else _job(row)


def _refuse_unset(tenant_id: str) -> None:
    if tenant_id.strip() == "":
        raise TenantUnset(
            "an unset tenant matches no rows under the policy, which is indistinguishable "
            "from a tenant that owns none; refused here instead"
        )


class PostgresDatabase:
    """The pool, the schema, and the lifecycle."""

    name: str = "postgres"

    def __init__(
        self,
        *,
        dsn: str,
        draws: Draws,
        kinds: Callable[["PostgresDatabase"], Kinds],
        schema: str | None = None,
        min_size: int = 1,
        timeouts: Timeouts = TIMEOUTS,
    ) -> None:
        self._dsn: str = dsn
        self.schema: str = resolve_schema(schema)
        self._draws: Draws = draws
        self._min_size: int = min_size
        self._timeouts: Timeouts = timeouts
        self._budget: ConnectionBudget | None = None
        self._pool: Any = None
        self._opening: asyncio.Lock = asyncio.Lock()
        self._waited: Callable[[float], None] | None = None
        self.kinds: Kinds = kinds(self)
        """Each kind of job, and the handler that `kinds` built for it."""

    def budget(self) -> ConnectionBudget:
        """The budget the pool opened under. Raises `RuntimeError` before the pool first opens."""
        if self._budget is None:
            raise RuntimeError("the pool has not opened, so it has no budget yet")
        return self._budget

    def _server_settings(self) -> dict[str, str]:
        return {
            "search_path": self.schema,
            "statement_timeout": _milliseconds(self._timeouts.statement),
            "idle_in_transaction_session_timeout": _milliseconds(
                self._timeouts.idle_in_transaction
            ),
        }

    async def _budgeted(self) -> ConnectionBudget:
        """The budget under the server's limits, read on one connection that closes before the
        pool opens. Raises `OverBudget` when the draws do not fit."""
        conn = await self.dedicated()
        try:
            limits = await server_limits(conn)
        finally:
            await conn.close()
        budget = budgeted(limits, self._draws)
        log.connections_budgeted(
            max_connections=limits.max_connections,
            reserved=limits.reserved,
            outside=dict(self._draws.outside),
            instances=self._draws.instances,
            in_process=dict(self._draws.in_process),
            pool=budget.pool,
        )
        return budget

    async def pool(self) -> Any:
        """One pool, created on first use so construction stays synchronous and cheap, and
        sized from the connection budget. Raises `OverBudget` when the draws do not fit.

        Guarded, because two coroutines arriving before it exists would each create one and
        the loser's would be leaked -- holding its connections until the process exits, with
        nothing to show for it. The lifespan opens the pool before any request is served, so
        the guard is for the next caller rather than for today's.
        """
        if self._pool is None:
            async with self._opening:
                if self._pool is None:
                    self._budget = await self._budgeted()
                    self._pool = await _driver().create_pool(
                        dsn=self._dsn,
                        min_size=min(self._min_size, self._budget.pool),
                        max_size=self._budget.pool,
                        command_timeout=self._timeouts.answer,
                        setup=_name_who_acts,
                        server_settings=self._server_settings(),
                    )
        return self._pool

    @asynccontextmanager
    async def connection(self) -> AsyncGenerator[Any]:
        """A connection from the pool, returned to it on exit. Raises `AcquireTimedOut` when none
        comes within `Timeouts.acquire`, `ConnectionRefused` when a new one does not open, and
        `ConnectionLost` when the block fails because the connection ended. Each tells the client
        to ask again after `Timeouts.acquire`.

        A wait that ends in a connection or in `AcquireTimedOut` goes to the recorder that
        `time_waits` set, if any. The wait includes the setup that names who acts, one round trip
        to Postgres. When the pool grows, it also includes opening a new connection. A refusal
        and a cancelled wait go to no recorder."""
        pool = await self.pool()
        retry_after = self.retry_after
        started = time.monotonic()
        try:
            conn = await pool.acquire(timeout=self._timeouts.acquire)
        except TimeoutError as waited:
            self._record_wait(started)
            raise AcquireTimedOut(
                f"no connection within {self._timeouts.acquire}s: all {self.budget().pool} "
                f"stayed in use, or a new one did not open in time",
                retry_after=retry_after,
            ) from waited
        except _refused_by_the_server() as refused:
            raise self._refused(refused) from refused
        try:
            self._record_wait(started)
            yield conn
        except failed_on_a_connection() as failed:
            if _ended(conn):
                raise ConnectionLost(
                    f"the connection ended during the unit of work: {type(failed).__name__}",
                    retry_after=retry_after,
                ) from failed
            raise
        finally:
            if _ended_but_still_lent(conn):
                conn.terminate()
            else:
                await pool.release(conn)

    @property
    def retry_after(self) -> int:
        """The whole seconds after which a unit of work that met `DatabaseUnavailable` runs
        again: `Timeouts.acquire`, rounded up."""
        return math.ceil(self._timeouts.acquire)

    def _refused(self, refused: BaseException) -> ConnectionRefused:
        return ConnectionRefused(
            f"a new connection did not open: {type(refused).__name__}",
            retry_after=self.retry_after,
        )

    async def dedicated(self) -> Any:
        """A connection of its own, outside the pool and its budget, with the same search path
        and bounds. The caller closes it. Raises `ConnectionRefused` when it does not open."""
        try:
            return await _driver().connect(
                dsn=self._dsn,
                timeout=self._timeouts.acquire,
                command_timeout=self._timeouts.answer,
                server_settings=self._server_settings(),
            )
        except _refused_by_the_server() as refused:
            raise self._refused(refused) from refused

    def connections(self) -> Connections:
        """The pool's connections now. Before the pool opens and after it closes, none in use.
        Before it first opens, a size of none. Safe to call from another thread, as a metric
        reader does."""
        pool = self._pool
        budget = self._budget
        most = 0 if budget is None else budget.pool
        if pool is None:
            return Connections(pool=self.schema, used=0, idle=0, max_size=most)
        idle = pool.get_idle_size()
        return Connections(pool=self.schema, used=pool.get_size() - idle, idle=idle, max_size=most)

    def time_waits(self, record: Callable[[float], None]) -> None:
        self._waited = record

    def _record_wait(self, started: float) -> None:
        if self._waited is not None:
            self._waited(time.monotonic() - started)

    def store(self, tenant_id: str) -> TaskStore:
        _refuse_unset(tenant_id)
        return PostgresTaskStore(self, tenant_id)

    def audit_events(self, tenant_id: str) -> AuditEvents:
        _refuse_unset(tenant_id)
        return PostgresAuditEvents(self, tenant_id)

    def jobs(self, tenant_id: str) -> Jobs:
        _refuse_unset(tenant_id)
        return PostgresJobs(self, tenant_id)

    @asynccontextmanager
    async def scoped(self, tenant_id: str) -> AsyncGenerator[Any]:
        """A connection inside a transaction that has set `tenant_id`, which every statement
        on tenant data runs in.

        Inside `app.store.jobs.fenced`, the transaction first holds the claim with
        `FOR KEY SHARE` until it ends, so no worker can claim the job while it runs. Raises
        `LeaseLost`, and rolls back, when the claim is no longer this attempt's, or is of another
        tenant."""
        async with self.connection() as conn, conn.transaction():
            await conn.execute(_SET_TENANT, TENANT_GUC, tenant_id)
            claim = fence()
            if claim is not None and (
                await conn.fetchval(_HOLD_CLAIM, UUID(claim.id), tenant_id, claim.token) is None
            ):
                raise LeaseLost(
                    f"attempt {claim.attempts} of job {claim.id} lost its lease, or opened a "
                    "transaction for a tenant that is not the job's"
                )
            yield conn

    async def check(self) -> str:
        async with self.connection() as conn:
            return await check(conn, self.schema)

    async def schema_version(self) -> str | None:
        async with self.connection() as conn:
            return await schema_version(conn, self.schema)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
