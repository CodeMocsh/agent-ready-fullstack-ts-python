"""Applying the schema, verifying it, and emitting it as a script.

Two modes and no third. `auto` applies what is missing; `check` refuses instead of applying,
which is what lets a role holding no `CREATE` call this on connect at all. There is
deliberately no mode that skips verifying — that buys a missing table discovered later, by
whichever query needed it, in production.

The version marker is `max(key)`, which is why `ddl.py` bands its keys and why the repair
band sorts above every other one.
"""

import os
from typing import Final, Literal

from app.store.conn import Conn, migration_lock, quote_ident, resolve_schema, search_path_sql
from app.store.ddl import APPLIED_ONCE_DDL, SCHEMA_ENTRY_KEY, statements

MigrateMode = Literal["auto", "check"]

MIGRATE_ENV: Final = "DB_MIGRATE"
MODES: Final[tuple[MigrateMode, ...]] = ("auto", "check")

_RECORD = "INSERT INTO applied_once(key) VALUES($1) ON CONFLICT DO NOTHING"


class MigrationError(RuntimeError):
    """The schema could not be brought to the version this code expects."""


class SchemaBehindError(MigrationError):
    """`check` mode found a database behind this code, and may not apply the difference."""


class SchemaTooNewError(MigrationError):
    """The database carries a later schema than this code knows about.

    Raised rather than shrugged at, because a newer schema means a newer deployment is
    running against the same database and this one would be writing rows to a shape it does
    not understand.
    """


class InvalidMigrateMode(ValueError):
    """`DB_MIGRATE` is neither `auto` nor `check`."""


def resolve_mode(mode: str | None = None) -> MigrateMode:
    """The validated mode: the argument, else `DB_MIGRATE`, else `auto`."""
    resolved = mode if mode is not None else os.environ.get(MIGRATE_ENV, "auto")
    if resolved not in MODES:
        raise InvalidMigrateMode(f"{MIGRATE_ENV}={resolved!r}: expected one of {MODES}")
    return resolved


def known_keys(schema: str | None = None) -> list[str]:
    """Every entry key this code carries, in the order it applies them."""
    return [key for key, _ in statements(schema)]


def known_version(schema: str | None = None) -> str:
    """The version marker this code expects a database to report."""
    return known_keys(schema)[-1]


def _ledger(schema: str | None) -> str:
    return f"{quote_ident(resolve_schema(schema))}.applied_once"


async def schema_version(conn: Conn, schema: str | None = None) -> str | None:
    """The applied version marker, or `None` on a database that was never migrated.

    Qualified rather than relying on the search path, because this runs *before* the search
    path is set — on a database where the schema may not exist yet.

    `COLLATE "C"` because the answer is compared against `known_version()` in Python. Without
    it the database picks the maximum under its own collation and this code picks it under
    byte order, and the two disagreeing means `check` mode reporting a current database as
    behind, or worse, a behind one as current.
    """
    ledger = _ledger(schema)
    if await conn.fetchval("SELECT to_regclass($1)", ledger) is None:
        return None
    return await conn.fetchval(f'SELECT max(key COLLATE "C") FROM {ledger}')


async def migrate(conn: Conn, schema: str | None = None, *, mode: str | None = None) -> str:
    """Bring `conn`'s database up to this code's schema, or verify that someone has."""
    resolved = resolve_mode(mode)
    target = known_version(schema)
    current = await schema_version(conn, schema)
    if current == target:
        return target
    if current is not None and current > target:
        raise SchemaTooNewError(
            f"the database reports schema {current!r} and this code knows {target!r}; "
            f"a newer deployment is using this database"
        )
    if resolved == "check":
        raise SchemaBehindError(
            f"the database reports schema {current!r} and this code needs {target!r}. "
            f"{MIGRATE_ENV}=check may not apply it: run the migration as a role that may "
            f"create, or apply deploy/schema.sql"
        )
    return await apply_schema(conn, schema)


async def apply_schema(conn: Conn, schema: str | None = None) -> str:
    """Apply every entry in order under an advisory lock, recording each in the ledger.

    Every entry is re-executed on every run rather than filtered against the ledger, because
    every entry is idempotent — that is the rule `ddl.py` enforces, and leaning on it here is
    what keeps this function short enough to read. The ledger exists to answer `max(key)` for
    `check` mode, not to decide what runs.
    """
    name = resolve_schema(schema)
    entries = statements(name)
    lock = migration_lock(name)
    await conn.execute("SELECT pg_advisory_lock($1)", lock)
    try:
        first_key, first_sql = entries[0]
        if first_key != SCHEMA_ENTRY_KEY:
            raise MigrationError(f"{SCHEMA_ENTRY_KEY} must sort first, not {first_key!r}")
        await conn.execute(first_sql)
        await conn.execute(search_path_sql(name))
        await conn.execute(APPLIED_ONCE_DDL)
        await conn.execute(_RECORD, first_key)
        for key, sql in entries[1:]:
            await conn.execute(sql)
            await conn.execute(_RECORD, key)
        return known_version(name)
    finally:
        await conn.execute("SELECT pg_advisory_unlock($1)", lock)


def emit_sql(schema: str | None = None) -> str:
    """The whole schema as a script, in the order `apply_schema` applies it.

    For a database whose own migration tool owns the DDL: apply this, then set
    `DB_MIGRATE=check`. It writes a ledger row for every key it emits, so `check` passes
    against it afterwards.
    """
    name = resolve_schema(schema)
    entries = statements(name)
    first_key, first_sql = entries[0]
    lines = [
        f"-- Schema for the {name!r} schema, emitted by `make schema`. Do not hand-edit.",
        "-- Apply in order, before the app starts. Then set DB_MIGRATE=check.",
        "-- Safe to re-apply: every statement is idempotent and every key is recorded once.",
        "",
        f"-- {first_key}",
        f"{first_sql};",
        "",
        f"{search_path_sql(name)};",
        "",
        f"{APPLIED_ONCE_DDL};",
        "",
        _record_line(first_key),
        "",
    ]
    for key, sql in entries[1:]:
        lines += [f"-- {key}", f"{sql};", _record_line(key), ""]
    return "\n".join(lines)


def _record_line(key: str) -> str:
    return f"INSERT INTO applied_once(key) VALUES('{key}') ON CONFLICT DO NOTHING;"
