"""Verifying the schema, applying it, and emitting it as a script.

**Two functions, not one function with a mode.** `check` is what the application runs at
boot; `apply` is what `make migrate` runs. A mode would have had one reachable value in each
process, which is dead configuration that reads like a choice — and worse, it would mean the
application importing the code that applies DDL. It does not: nothing on the request path can
reach `apply`, whatever credential the process was handed.

`check` is not a convenience. **Postgres tests privilege before existence**, so
`CREATE TABLE IF NOT EXISTS` against an already-correct table fails with `permission denied
for schema` for a role holding no `CREATE`. A least-privilege application could not start at
all without a path that only reads.

**The comparison is the set of keys, not the highest one.** `check` asks whether the ledger
holds every entry this build carries. `apply` runs every entry, every time, under one lock, and
leans on each one being idempotent. Nothing compares a single scalar and returns early, so an
entry added below an existing key runs on the next release step like any other.

`known_version` and `schema_version` still answer `max(key)`. That string is what `make migrate`
prints and what `Database.schema_version()` reports. No decision reads it.

**Every wait of `apply` has a bound.** `MigrationBounds` says how long each attempt to take a lock
lasts, how long `apply` waits for one lock, and how long one statement may run. When a wait for a
lock passes its bound, `LockNotGrantedError` names each session that holds a lock on the schema or
the migration lock. `docs/deployment.md` says why an attempt is short.
"""

import hashlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final

from app.pacing import backoff
from app.store.acting import MIGRATION_ACTING_SQL
from app.store.conn import Conn, migration_lock, quote_ident, resolve_schema, search_path_sql
from app.store.ddl import APPLIED_ONCE_DDL, SCHEMA_ENTRY_KEY, statements
from app.store.roles import owner_role, writes_withheld_sql
from app.store.timeouts import SMALLEST_BOUND, milliseconds

_RECORD = "INSERT INTO applied_once(key) VALUES($1) ON CONFLICT DO NOTHING"

_MAY_SET_ROLE = (
    "SELECT CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1) "
    "THEN pg_has_role(current_user, $1, 'MEMBER') ELSE false END"
)
"""Two questions, and `CASE` rather than `AND` because the order is load-bearing.

Roles are cluster-wide, so the role existing says nothing about this database — and
`pg_has_role` raises outright on a role that is not there. `AND` does not promise to evaluate
the existence test first; `CASE` does.
"""


_TRY_LOCK = "SELECT pg_try_advisory_lock($1)"

_HOLDERS = """
SELECT string_agg(
  format('pid %s, role %s, application %L, %s, transaction since %s: %s',
         a.pid, a.usename, a.application_name, coalesce(a.state, 'state hidden'),
         CASE WHEN a.state IS NULL THEN 'hidden' ELSE coalesce(a.xact_start::text, 'none') END,
         a.query),
  chr(10) ORDER BY a.xact_start NULLS LAST, a.pid)
FROM pg_stat_activity a
WHERE a.pid <> pg_backend_pid() AND a.pid IN (
  SELECT l.pid FROM pg_locks l
  LEFT JOIN pg_class c ON c.oid = l.relation
  LEFT JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE l.granted
    AND l.database = (SELECT oid FROM pg_database WHERE datname = current_database())
    AND (n.nspname = $1
         OR (l.locktype = 'advisory' AND l.classid = $2 AND l.objid = $3 AND l.objsubid = 1)))
"""


@dataclass(frozen=True)
class MigrationBounds:
    """How long each wait of `apply` may last, in seconds.

    An attempt to take a lock ends after `attempt`. The next attempt starts after a pause that
    starts at `attempt` and doubles up to `longest_pause`. `apply` raises `LockNotGrantedError`
    when a further pause would end more than `lock_wait` after the first attempt for that lock.
    Postgres cancels a statement of `apply` that runs longer than `statement`, and asyncpg raises
    `QueryCanceledError`.
    """

    attempt: float = 1.0
    longest_pause: float = 8.0
    lock_wait: float = 600.0
    statement: float = 600.0

    def __post_init__(self) -> None:
        if min(self.attempt, self.longest_pause, self.lock_wait, self.statement) < SMALLEST_BOUND:
            raise ValueError(f"{self} holds a bound under a millisecond, which Postgres ignores")


MIGRATION_BOUNDS: Final = MigrationBounds()


class MigrationError(RuntimeError):
    """The schema is not the one this code expects, and cannot be made so from here."""


class SchemaBehindError(MigrationError):
    """The database is behind this code, and this process may not apply the difference."""


class SchemaTooNewError(MigrationError):
    """The database carries a later schema than this build knows about.

    Raised by both `check` and `apply`. A newer schema means a newer release has already
    migrated this database, and neither serving queries written against the older shape nor
    re-applying the older entries over it is something to guess at.
    """


class LockNotGrantedError(MigrationError):
    """`apply` stopped waiting for a lock after `MigrationBounds.lock_wait`.

    Each statement ran whole or did not run, and running `apply` again applies what is missing.
    The message names the statement, and each session that held a lock on the schema or the
    migration lock when `apply` stopped, oldest transaction first.
    """


def known_keys(schema: str | None = None) -> list[str]:
    """Every entry key this code carries, in the order it applies them."""
    return [key for key, _ in statements(schema)]


def known_version(schema: str | None = None) -> str:
    """The last key, for `make migrate` to print and the store to report. Decides nothing."""
    return known_keys(schema)[-1]


def entry_hashes(schema: str | None = None) -> dict[str, str]:
    """A hash of each entry body, by key. Read by the gate, never at run time.

    `.schema-baseline.json` records these and `test_no_shipped_entry_body_has_changed` compares
    against them.
    """
    return {key: hashlib.sha256(sql.encode()).hexdigest() for key, sql in statements(schema)}


def _ledger(schema: str | None) -> str:
    return f"{quote_ident(resolve_schema(schema))}.applied_once"


async def applied_keys(conn: Conn, schema: str | None = None) -> set[str]:
    """Every entry key the ledger holds, and empty where it holds none or does not exist.

    Qualified rather than relying on the search path: this runs before the search path is set,
    on a database whose schema may not exist yet.
    """
    ledger = _ledger(schema)
    if await conn.fetchval("SELECT to_regclass($1)", ledger) is None:
        return set()
    joined = await conn.fetchval(f"SELECT string_agg(key, chr(10)) FROM {ledger}")
    return set() if joined is None else set(joined.split("\n"))


async def schema_version(conn: Conn, schema: str | None = None) -> str | None:
    """The last key the ledger holds, or `None` on a database that was never migrated.

    Reported, never compared. `Database.schema_version()` surfaces it and `make migrate`
    prints it, so it is a string a person reads.
    """
    return max(await applied_keys(conn, schema), default=None)


async def check(conn: Conn, schema: str | None = None) -> str:
    """Verify that someone has applied this code's schema. Reads the ledger and nothing else.

    **Every entry key must be present, and no other.** A missing key refuses because the
    columns this build names may not be there. An unknown key refuses because a newer release
    has already migrated this database, and this one would be writing rows to a shape it does
    not understand.

    Unknown keys could be tolerated — every migration here is additive, so an older build can
    still write what it knows about — and this deliberately does not. "Probably compatible" is
    a judgement call made at startup by a process with no way to check it, and the failure it
    would let through is silent. The cost is stated in
    `docs/adr/template/0003-the-application-never-applies-ddl.md`: a rolling deploy has a window in
    which a restarting instance of the previous release will not come back up.
    """
    applied = await applied_keys(conn, schema)
    known = known_keys(schema)
    unknown = _unknown_keys(applied, known)
    if unknown:
        raise SchemaTooNewError(
            f"the database has applied {unknown!r}, which this build does not carry. "
            f"A newer release has migrated it, so this build would be writing rows to a "
            f"shape it does not know. Deploy the newer build, or roll the schema back."
        )
    missing = [key for key in known if key not in applied]
    if missing:
        raise SchemaBehindError(
            f"the database has not applied {missing!r}. This process holds no rights to apply "
            f"it -- run `make migrate` as part of the release, before the new version serves "
            f"traffic."
        )
    return known_version(schema)


async def apply(
    conn: Conn, schema: str | None = None, bounds: MigrationBounds = MIGRATION_BOUNDS
) -> str:
    """Bring the database up to this code's schema. What `make migrate` runs.

    Every entry runs on every call. Each one is idempotent, so a database that is already
    current is unchanged by the run, and a release hook that fires more than once costs one
    pass over the entries. Nothing here compares a version and decides to skip: that is what
    makes an entry sorting below an existing key run at all.

    Every statement it runs, the first included, has the bounds in `bounds`. Raises
    `LockNotGrantedError` when a lock it needs stays held past `bounds.lock_wait`.
    """
    await conn.execute(f"SET lock_timeout = {milliseconds(bounds.attempt)}")
    await conn.execute(f"SET statement_timeout = {milliseconds(bounds.statement)}")
    unknown = _unknown_keys(await applied_keys(conn, schema), known_keys(schema))
    if unknown:
        raise SchemaTooNewError(
            f"the database has applied {unknown!r}, which this migrator does not carry; "
            f"a newer release has already migrated it. Running an older release's migration "
            f"step against it is a deploy-order mistake, not something to reconcile here."
        )
    return await _apply_all(conn, schema, bounds)


def _unknown_keys(applied: set[str], known: list[str]) -> list[str]:
    """Keys the ledger holds that this build does not carry, sorted for the message."""
    return sorted(applied - set(known))


def _lock_not_available() -> type[Exception]:
    import asyncpg

    return asyncpg.LockNotAvailableError


async def _executed(conn: Conn, query: str, args: tuple[Any, ...]) -> bool:
    """Whether `query` ran. False when its lock wait passed `lock_timeout`."""
    try:
        await conn.execute(query, *args)
    except _lock_not_available():
        return False
    return True


@dataclass(frozen=True)
class _Locks:
    """One run of `apply`: its connection, its schema and its bounds."""

    conn: Conn
    schema: str
    bounds: MigrationBounds

    async def run(self, what: str, query: str, *args: Any) -> None:
        """Run `query`, again after each wait for a lock that passes `MigrationBounds.attempt`."""
        await self._until(what, lambda: _executed(self.conn, query, args))

    async def take(self, lock: int) -> None:
        """Take the session advisory lock `lock`."""
        await self._until("the migration lock", lambda: self.conn.fetchval(_TRY_LOCK, lock))

    async def _until(self, what: str, granted: Callable[[], Awaitable[bool]]) -> None:
        stops = time.monotonic() + self.bounds.lock_wait
        pause = self.bounds.attempt
        while not await granted():
            if time.monotonic() + pause > stops:
                raise LockNotGrantedError(
                    f"{what}: no lock within {self.bounds.lock_wait:g} s. The sessions that hold a "
                    f"lock on the schema or the migration lock:\n{await self._holders()}"
                )
            await backoff(pause)
            pause = min(2 * pause, self.bounds.longest_pause)

    async def _holders(self) -> str:
        """Each holder, read as the role that connected, which `RESET ROLE` restores. A role
        without `pg_read_all_stats` reads no query of another role's session."""
        await self.conn.execute("RESET ROLE")
        lock = migration_lock(self.schema)
        holders = await self.conn.fetchval(
            _HOLDERS, self.schema, (lock >> 32) & 0xFFFFFFFF, lock & 0xFFFFFFFF
        )
        if holders is None:
            return "no session holds one now."
        return str(holders)


async def _become_owner(conn: Conn, schema: str | None) -> None:
    """`SET ROLE` to the owning role where that is possible, and carry on where it is not.

    Objects created without it are owned by whoever connected, so `ALTER DEFAULT PRIVILEGES
    FOR ROLE <schema>_owner` binds nothing and the application is denied at *query* time
    rather than here. Carrying on is the bootstrap: a developer pointing at their own Postgres
    has no roles provisioned, and `FORCE ROW LEVEL SECURITY` keeps their objects policy-bound
    regardless of who owns them.
    """
    owner = owner_role(schema)
    if await conn.fetchval(_MAY_SET_ROLE, owner):
        await conn.execute(f"SET ROLE {quote_ident(owner)}")


async def _withhold_writes(locks: _Locks) -> None:
    """Run `writes_withheld_sql`: no write on the ledger for the application role, and only an
    insert on the audit events into the columns the database does not fill.

    The role that runs `check` cannot forge its own answer, and the application cannot change,
    remove, backdate or reorder an event. Every `apply` runs it after the last entry, so it sees
    every column a repair adds, and a database whose roles were provisioned after its first
    migration is caught up by the next one.
    """
    for statement in writes_withheld_sql(locks.schema):
        await locks.run("the writes withheld from the application role", statement)


async def _apply_all(conn: Conn, schema: str | None, bounds: MigrationBounds) -> str:
    """Every entry in order under an advisory lock, recording each in the ledger, acting as
    `MIGRATION_ACTOR`.

    Every entry is re-executed rather than filtered against the ledger, because every entry is
    idempotent — that is the rule `ddl.py` enforces, and leaning on it here is what keeps this
    short enough to read. The ledger records what ran, for `check` to read. It never decides
    what runs.
    """
    name = resolve_schema(schema)
    entries = statements(name)
    locks = _Locks(conn, name, bounds)
    lock = migration_lock(name)
    await locks.take(lock)
    try:
        first_key, first_sql = entries[0]
        if first_key != SCHEMA_ENTRY_KEY:
            raise MigrationError(f"{SCHEMA_ENTRY_KEY} must sort first, not {first_key!r}")
        await locks.run(first_key, first_sql)
        await _become_owner(conn, name)
        await conn.execute(search_path_sql(name))
        await conn.execute(MIGRATION_ACTING_SQL)
        await locks.run("the ledger", APPLIED_ONCE_DDL)
        await locks.run("the ledger", _RECORD, first_key)
        for key, sql in entries[1:]:
            await locks.run(key, sql)
            await locks.run("the ledger", _RECORD, key)
        await _withhold_writes(locks)
        return known_version(name)
    finally:
        await conn.execute("SELECT pg_advisory_unlock($1)", lock)


def emit_sql(schema: str | None = None) -> str:
    """The whole schema as a script, in the order `apply` applies it.

    For a database whose own migration tool owns the DDL. Unlike `make migrate` this cannot
    `SET ROLE` for you, so the header says to — objects created as the wrong role are denied
    at query time rather than here.
    """
    name = resolve_schema(schema)
    entries = statements(name)
    first_key, first_sql = entries[0]
    lines = [
        f"-- Schema for the {name!r} schema, emitted by `make schema`. Do not hand-edit.",
        f"-- Apply as {owner_role(name)}, or after `SET ROLE {owner_role(name)}`: objects",
        "-- created by another role fall outside its default privileges and the application",
        "-- is then refused at query time rather than here.",
        "-- Safe to re-apply: every statement is idempotent and every key is recorded once.",
        "",
        f"-- {first_key}",
        f"{first_sql};",
        "",
        f"{search_path_sql(name)};",
        f"{MIGRATION_ACTING_SQL};",
        "",
        f"{APPLIED_ONCE_DDL};",
        "",
        _record_line(first_key),
        "",
    ]
    for key, sql in entries[1:]:
        lines += [f"-- {key}", f"{sql};", _record_line(key), ""]
    lines += ["-- The writes the application role is never granted.", ""]
    lines += [f"{statement};" for statement in writes_withheld_sql(name)]
    return "\n".join([*lines, ""])


def _record_line(key: str) -> str:
    return f"INSERT INTO applied_once(key) VALUES('{key}') ON CONFLICT DO NOTHING;"
