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

**No environment variable is read here.** The DSN and the schema are constructor arguments and
`wiring.py` is the only reader, which is what lets one process hold two of these at once —
what the contract suite does.
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

from app.models import CreateTaskBody, Task, UpdateTaskBody
from app.store import TaskStore, TenantUnset
from app.store.conn import resolve_schema
from app.store.ddl import TENANT_GUC
from app.store.migrate import check, schema_version

_SET_TENANT = "SELECT set_config($1, $2, true)"
_LIST = "SELECT id, title, done FROM tasks ORDER BY seq"
_CREATE = "INSERT INTO tasks (id, tenant_id, title) VALUES ($1, $2, $3) RETURNING id, title, done"
_UPDATE = "UPDATE tasks SET done = $2 WHERE id = $1 RETURNING id, title, done"
_REMOVE = "DELETE FROM tasks WHERE id = $1 RETURNING id"


def _driver() -> Any:
    """asyncpg, declared as the untyped boundary it is and imported where it is needed.

    It ships no annotations, so every `Connection` and `Pool` reached through it is unknown to
    the type checker. Saying `Any` once, here, is what keeps that from becoming a suppression
    at each call site — and `AGENTS.md` bans the spelling of a suppression outright.

    Imported inside the function so that importing this module costs nothing on a process that
    never opens a connection, and so a project that deletes asyncpg still starts.
    """
    import asyncpg

    return asyncpg


def _task(row: Any) -> Task:
    return Task(id=str(row["id"]), title=row["title"], done=row["done"])


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

    @asynccontextmanager
    async def _scoped(self) -> AsyncGenerator[Any]:
        pool = await self._database.pool()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(_SET_TENANT, TENANT_GUC, self._tenant_id)
            yield conn

    async def list(self) -> list[Task]:
        async with self._scoped() as conn:
            return [_task(row) for row in await conn.fetch(_LIST)]

    async def create(self, body: CreateTaskBody) -> Task:
        async with self._scoped() as conn:
            return _task(await conn.fetchrow(_CREATE, uuid4(), self._tenant_id, body.title))

    async def update(self, id: str, body: UpdateTaskBody) -> Task | None:
        parsed = _as_id(id)
        if parsed is None:
            return None
        async with self._scoped() as conn:
            row = await conn.fetchrow(_UPDATE, parsed, body.done)
            return None if row is None else _task(row)

    async def remove(self, id: str) -> bool:
        parsed = _as_id(id)
        if parsed is None:
            return False
        async with self._scoped() as conn:
            return await conn.fetchval(_REMOVE, parsed) is not None


class PostgresDatabase:
    """The pool, the schema, and the lifecycle."""

    name: str = "postgres"

    def __init__(
        self,
        *,
        dsn: str,
        schema: str | None = None,
        min_size: int = 1,
        max_size: int = 10,
    ) -> None:
        self._dsn: str = dsn
        self._schema: str = resolve_schema(schema)
        self._min_size: int = min_size
        self._max_size: int = max_size
        self._pool: Any = None
        self._opening: asyncio.Lock = asyncio.Lock()

    async def pool(self) -> Any:
        """One pool, created on first use so construction stays synchronous and cheap.

        Guarded, because two coroutines arriving before it exists would each create one and
        the loser's would be leaked -- holding its connections until the process exits, with
        nothing to show for it. The lifespan opens the pool before any request is served, so
        the guard is for the next caller rather than for today's.
        """
        if self._pool is None:
            async with self._opening:
                if self._pool is None:
                    self._pool = await _driver().create_pool(
                        dsn=self._dsn,
                        min_size=self._min_size,
                        max_size=self._max_size,
                        server_settings={"search_path": self._schema},
                    )
        return self._pool

    def store(self, tenant_id: str) -> TaskStore:
        if tenant_id.strip() == "":
            raise TenantUnset(
                "an unset tenant matches no rows under the policy, which is indistinguishable "
                "from a tenant that owns none; refused here instead"
            )
        return PostgresTaskStore(self, tenant_id)

    async def check(self) -> str:
        pool = await self.pool()
        async with pool.acquire() as conn:
            return await check(conn, self._schema)

    async def schema_version(self) -> str | None:
        pool = await self.pool()
        async with pool.acquire() as conn:
            return await schema_version(conn, self._schema)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
