"""The schema, asserted without a Postgres to apply it to.

`ddl.py` is data, so most of what can go wrong with it is checkable here, in milliseconds,
in the fast tier. `tests/test_postgres.py` covers only what needs a real server.

`migrate()` is drivable by a fake because `Conn` is deliberately two methods. That is the
whole reason it is two methods.
"""

from pathlib import Path
from typing import Any

import pytest

from app.store import ddl
from app.store.conn import (
    DEFAULT_SCHEMA,
    InvalidSchemaName,
    migration_lock,
    resolve_schema,
)
from app.store.migrate import (
    InvalidMigrateMode,
    SchemaBehindError,
    SchemaTooNewError,
    emit_sql,
    known_keys,
    known_version,
    migrate,
    resolve_mode,
)

SCHEMA_SQL = Path(__file__).resolve().parents[2] / "deploy" / "schema.sql"

ADD_COLUMN = "ADD COLUMN IF NOT EXISTS"
CREATE_TABLE = "CREATE TABLE IF NOT EXISTS"


class FakeConn:
    """`Conn` with no database behind it. Records what it was asked to run."""

    def __init__(self, version: str | None = None) -> None:
        self.executed: list[str] = []
        self.recorded: list[str] = []
        self._version: str | None = version

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append(query)
        if query.startswith("INSERT INTO applied_once"):
            self.recorded.append(str(args[0]))
        return "OK"

    async def fetchval(self, query: str, *_args: Any) -> Any:
        if "to_regclass" in query:
            return None if self._version is None else 12345
        if "max(key" in query:
            return self._version
        raise AssertionError(f"the fake was asked something it does not answer: {query}")


def stray_semicolons(sql: str) -> int:
    """Semicolons outside dollar-quoted bodies, string literals and `--` comments.

    An entry is one statement, so it carries no terminator of its own: `emit_sql` adds one and
    the ledger records one key per statement. Any semicolon this finds is a second statement
    hiding in an entry.

    Written here rather than in `app/` because it asserts a property of the data rather than
    doing any work at run time.
    """
    count = 0
    index = 0
    while index < len(sql):
        if sql.startswith("--", index):
            index = sql.find("\n", index)
            if index == -1:
                break
        elif sql[index] == "'":
            index = sql.find("'", index + 1)
            if index == -1:
                break
        elif sql[index] == "$":
            end = sql.find("$", index + 1)
            tag = sql[index : end + 1] if end != -1 else "$"
            closing = sql.find(tag, end + 1)
            index = closing + len(tag) - 1 if closing != -1 else len(sql)
        elif sql[index] == ";":
            count += 1
        index += 1
    return count


def test_every_key_is_unique() -> None:
    keys = [key for key, _ in ddl.SCHEMA]
    assert len(keys) == len(set(keys))


def test_the_literal_list_is_already_in_applied_order() -> None:
    assert [key for key, _ in ddl.SCHEMA] == sorted(key for key, _ in ddl.SCHEMA)


def test_the_schema_entry_sorts_first() -> None:
    assert known_keys()[0] == ddl.SCHEMA_ENTRY_KEY


def test_every_entry_is_exactly_one_statement() -> None:
    for key, sql in ddl.statements():
        assert stray_semicolons(sql) == 0, (
            f"{key} contains a `;` outside a quoted body, so it is more than one statement. "
            f"`emit_sql` joins entries with `;` and the ledger records one key per statement."
        )


def test_the_repair_band_sorts_above_every_other_band() -> None:
    repairs = [key for key in known_keys() if key.startswith(ddl.REPAIR_BAND)]
    others = [key for key in known_keys() if not key.startswith(ddl.REPAIR_BAND)]
    for repair in repairs:
        assert all(repair > other for other in others), (
            f"{repair} does not sort above every other entry. `migrate()` compares max(key) "
            f"and returns early when the database is not behind, so a repair below an "
            f"existing entry never runs at all."
        )


def test_a_column_added_by_an_alter_is_also_in_its_create() -> None:
    entries = ddl.statements()
    creates = {
        body.split(CREATE_TABLE, 1)[1].split()[0]: body
        for _, body in entries
        if CREATE_TABLE in body
    }
    assert creates, "there is no CREATE TABLE in the schema at all"
    for key, sql in entries:
        if ADD_COLUMN not in sql:
            continue
        table = sql.split("ALTER TABLE", 1)[1].split()[0]
        column = sql.split(ADD_COLUMN, 1)[1].split()[0]
        assert table in creates, f"{key} alters {table}, which has no CREATE entry"
        assert column in creates[table], (
            f"{key} adds {table}.{column} and the CREATE for {table} does not mention it. "
            f"A column has to land twice: `CREATE TABLE IF NOT EXISTS` skips entirely once "
            f"the table exists, so the CREATE alone reaches no existing database and the "
            f"ALTER alone leaves the CREATE lying about the table."
        )


def test_known_version_is_the_last_key() -> None:
    assert known_version() == known_keys()[-1]


def test_a_schema_name_that_is_not_an_identifier_is_refused() -> None:
    for hostile in ('app"; DROP SCHEMA public', "App", "1app", "", "a" * 64, "public schema"):
        with pytest.raises(InvalidSchemaName):
            resolve_schema(hostile)


def test_a_plain_identifier_is_accepted() -> None:
    assert resolve_schema("tasks_app") == "tasks_app"
    assert resolve_schema(None) == DEFAULT_SCHEMA


def test_the_migration_lock_is_stable_and_differs_per_schema() -> None:
    assert migration_lock("app") == migration_lock("app")
    assert migration_lock("app") != migration_lock("other")
    assert -(2**63) <= migration_lock("app") < 2**63


def test_an_unknown_migrate_mode_is_refused() -> None:
    with pytest.raises(InvalidMigrateMode):
        resolve_mode("never")


async def test_migrate_applies_every_entry_to_an_empty_database() -> None:
    conn = FakeConn(version=None)

    assert await migrate(conn, "app", mode="auto") == known_version()

    assert conn.recorded == known_keys()
    assert any("pg_advisory_lock" in query for query in conn.executed)
    assert any("pg_advisory_unlock" in query for query in conn.executed)
    assert conn.executed.index('SET search_path TO "app"') < conn.executed.index(
        ddl.APPLIED_ONCE_DDL
    )


async def test_migrate_does_nothing_when_the_database_is_current() -> None:
    conn = FakeConn(version=known_version())

    assert await migrate(conn, "app", mode="auto") == known_version()

    assert conn.executed == []


async def test_check_mode_refuses_to_apply_and_says_why() -> None:
    conn = FakeConn(version=None)

    with pytest.raises(SchemaBehindError, match="check may not apply"):
        await migrate(conn, "app", mode="check")

    assert conn.executed == []


async def test_check_mode_passes_against_a_current_database() -> None:
    conn = FakeConn(version=known_version())

    assert await migrate(conn, "app", mode="check") == known_version()


async def test_a_database_newer_than_this_code_is_refused_in_both_modes() -> None:
    for mode in ("auto", "check"):
        conn = FakeConn(version="9999_from_the_future")
        with pytest.raises(SchemaTooNewError):
            await migrate(conn, "app", mode=mode)
        assert conn.executed == []


def test_the_committed_schema_sql_is_what_the_generator_emits() -> None:
    assert SCHEMA_SQL.exists(), f"{SCHEMA_SQL} is missing; run `make schema`"
    assert SCHEMA_SQL.read_text() == emit_sql(DEFAULT_SCHEMA), (
        "deploy/schema.sql does not match app/store/ddl.py. Run `make schema` and commit "
        "the result, the same way openapi.json follows the models."
    )


def test_the_emitted_script_records_every_key() -> None:
    emitted = emit_sql(DEFAULT_SCHEMA)
    for key in known_keys(DEFAULT_SCHEMA):
        assert f"VALUES('{key}')" in emitted, (
            f"{key} is applied by the script and never recorded, so DB_MIGRATE=check would "
            f"report a database that ran the whole script as behind."
        )
