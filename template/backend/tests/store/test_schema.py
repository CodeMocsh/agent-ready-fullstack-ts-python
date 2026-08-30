"""The schema, asserted without a Postgres to apply it to.

`ddl.py` is data, so most of what can go wrong with it is checkable here, in milliseconds,
in the fast tier. `tests/integration/test_postgres.py` covers only what needs a real server.

`migrate()` is drivable by a fake because `Conn` is deliberately two methods. That is the
whole reason it is two methods.
"""

import json
import re
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
from app.store.ddl import statements
from app.store.migrate import (
    SchemaBehindError,
    SchemaTooNewError,
    apply,
    check,
    emit_sql,
    entry_hashes,
    known_keys,
    known_version,
)
from app.store.roles import app_role, emit_roles_sql, owner_role
from devtools.schema import SCHEMA_BASELINE

SCHEMA_SQL = Path(__file__).resolve().parents[3] / "deploy" / "schema.sql"
ROLES_SQL = Path(__file__).resolve().parents[3] / "deploy" / "roles.sql"

ADD_COLUMN = "ADD COLUMN IF NOT EXISTS"
CREATE_TABLE = "CREATE TABLE IF NOT EXISTS"


NEVER_MIGRATED: list[str] = []
"""What the ledger holds on a database nothing has applied. The `applied` default is every key
this build carries, so a test that wants a current database says nothing."""


class FakeConn:
    """`Conn` with no database behind it. Records what it was asked to run.

    `applied` is what the ledger holds, and it drives every answer this fake gives."""

    def __init__(self, applied: list[str] | None = None, *, may_set_role: bool = False) -> None:
        self.executed: list[str] = []
        self.recorded: list[str] = []
        self._applied: list[str] = known_keys("app") if applied is None else applied
        self._may_set_role: bool = may_set_role

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append(query)
        if query.startswith("INSERT INTO applied_once"):
            self.recorded.append(str(args[0]))
        return "OK"

    async def fetchval(self, query: str, *_args: Any) -> Any:
        if "to_regclass" in query:
            return None if not self._applied else 12345
        if "string_agg" in query:
            return "\n".join(sorted(self._applied)) or None
        if "pg_has_role" in query:
            return self._may_set_role
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


def repairs_out_of_step(entries: list[tuple[str, str]]) -> list[str]:
    """Every way a repair entry and the `CREATE` it repairs can disagree.

    A column lands twice: in its table's `CREATE`, which stays the whole truth about the table,
    and as an `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` keyed above it. The `CREATE` alone
    reaches no database that already has the table, the `ALTER` alone leaves the `CREATE` lying
    about it, and an `ALTER` keyed below its `CREATE` runs before the table exists.

    Taken as an argument rather than read from `ddl.SCHEMA`, because the template ships no
    repair yet: driven only by the live schema every assertion here would be vacuous.
    """
    creates = {
        body.split(CREATE_TABLE, 1)[1].split()[0]: (key, body)
        for key, body in entries
        if CREATE_TABLE in body
    }
    found: list[str] = []
    for key, sql in entries:
        if ADD_COLUMN not in sql:
            continue
        table = sql.split("ALTER TABLE", 1)[1].split()[0]
        column = sql.split(ADD_COLUMN, 1)[1].split()[0]
        if table not in creates:
            found.append(f"{key} alters {table}, which has no CREATE entry")
            continue
        created_by, create = creates[table]
        if column not in create:
            found.append(f"{key} adds {table}.{column} and the CREATE for {table} does not name it")
        if key < created_by:
            found.append(f"{key} repairs {table} and sorts below {created_by}, which creates it")
    return found


A_TASKS_CREATE = ("0010_tasks", f"{CREATE_TABLE} tasks (\n  id uuid PRIMARY KEY,\n  note text\n)")
A_TASKS_REPAIR = ("0200_tasks_note", f"ALTER TABLE tasks {ADD_COLUMN} note text")


def test_every_repair_is_in_step_with_its_create() -> None:
    assert ddl.statements(), "there are no entries at all"
    assert repairs_out_of_step(ddl.statements()) == []


def test_a_repair_keyed_below_its_create_is_reported() -> None:
    """Entries run in key order, so this one runs against a table that does not exist yet."""
    early = ("0005_tasks_note", A_TASKS_REPAIR[1])

    assert repairs_out_of_step([early, A_TASKS_CREATE]) == [
        "0005_tasks_note repairs tasks and sorts below 0010_tasks, which creates it"
    ]


def test_a_repair_whose_column_is_missing_from_its_create_is_reported() -> None:
    """The `CREATE` stays the whole truth about the table, or a fresh database and an existing
    one end up with different columns."""
    bare = ("0010_tasks", f"{CREATE_TABLE} tasks (\n  id uuid PRIMARY KEY\n)")

    assert repairs_out_of_step([bare, A_TASKS_REPAIR]) == [
        "0200_tasks_note adds tasks.note and the CREATE for tasks does not name it"
    ]


def test_a_repair_for_a_table_nothing_creates_is_reported() -> None:
    assert repairs_out_of_step([A_TASKS_REPAIR]) == [
        "0200_tasks_note alters tasks, which has no CREATE entry"
    ]


def test_a_repair_in_step_with_its_create_is_not_reported() -> None:
    assert repairs_out_of_step([A_TASKS_CREATE, A_TASKS_REPAIR]) == []


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


async def test_apply_runs_every_entry_against_an_empty_database() -> None:
    conn = FakeConn(NEVER_MIGRATED)

    assert await apply(conn, "app") == known_version()

    assert conn.recorded == known_keys()
    assert any("pg_advisory_lock" in query for query in conn.executed)
    assert any("pg_advisory_unlock" in query for query in conn.executed)
    assert conn.executed.index('SET search_path TO "app"') < conn.executed.index(
        ddl.APPLIED_ONCE_DDL
    )


async def test_apply_becomes_the_owner_where_it_may_and_carries_on_where_it_may_not() -> None:
    """Objects created without `SET ROLE` fall outside the owner's default privileges, and the
    application is refused at query time rather than here. Where the role does not exist at
    all -- a developer's own Postgres -- carrying on is the bootstrap, and `FORCE` keeps those
    objects policy-bound whoever owns them."""
    permitted = FakeConn(NEVER_MIGRATED, may_set_role=True)
    await apply(permitted, "app")
    assert 'SET ROLE "app_owner"' in permitted.executed

    bootstrap = FakeConn(NEVER_MIGRATED, may_set_role=False)
    await apply(bootstrap, "app")
    assert not any(query.startswith("SET ROLE") for query in bootstrap.executed)
    assert bootstrap.recorded == known_keys()


async def test_apply_runs_every_entry_again_on_a_database_that_is_already_current() -> None:
    """No early return, and that is the point.

    Every entry is idempotent, so the second run changes nothing. Skipping on a version
    comparison is what would make an entry keyed below an existing one invisible for ever.
    """
    conn = FakeConn()

    assert await apply(conn, "app") == known_version()

    assert conn.recorded == known_keys()
    assert any("pg_advisory_lock" in query for query in conn.executed)


async def test_apply_lands_an_entry_keyed_below_the_ones_already_applied() -> None:
    """The failure a `max(key)` comparison could not see, and now cannot happen.

    A repair banded under an existing entry leaves the highest applied key exactly where it
    was. Nothing here reads the highest key, so the entry runs like any other.
    """
    keys = known_keys("app")
    conn = FakeConn([*keys[:1], *keys[2:]])

    await apply(conn, "app")

    assert keys[1] in conn.recorded


async def test_apply_revokes_the_ledger_on_every_run() -> None:
    """The bootstrap order is migrate first, provision roles second, so the run that creates
    the tables is usually the one with no role to revoke from yet. Every run revokes, so the
    next one catches up. Otherwise that database keeps an application role able to write its
    own ledger, and `check` could be handed an answer it forged."""
    conn = FakeConn()

    await apply(conn, "app")

    assert any("REVOKE" in query and "applied_once" in query for query in conn.executed)


async def test_check_never_writes_anything() -> None:
    """The property that lets a role holding no `CREATE` run it: it only ever reads."""
    conn = FakeConn()

    assert await check(conn, "app") == known_version()

    assert conn.executed == []


async def test_check_refuses_a_database_missing_any_entry_and_names_the_fix() -> None:
    conn = FakeConn(NEVER_MIGRATED)

    with pytest.raises(SchemaBehindError, match="make migrate"):
        await check(conn, "app")

    assert conn.executed == []


async def test_check_refuses_a_database_missing_only_an_entry_below_the_highest_key() -> None:
    """The highest applied key is unchanged here, so a marker comparison serves this database.
    It is missing whatever that entry creates, and every query naming it fails."""
    keys = known_keys("app")
    conn = FakeConn([*keys[:1], *keys[2:]])

    with pytest.raises(SchemaBehindError, match=keys[1]):
        await check(conn, "app")


async def test_check_refuses_a_database_carrying_an_entry_this_build_does_not() -> None:
    """Tolerating it would be a compatibility judgement made at startup by a process with no
    way to verify it, and the failure it lets through -- an older build writing rows to a
    shape it does not know about -- is silent. `docs/adr/0003` records what refusing costs.
    """
    conn = FakeConn([*known_keys("app"), "9999_from_the_future"])

    with pytest.raises(SchemaTooNewError, match="newer release"):
        await check(conn, "app")

    assert conn.executed == []


async def test_apply_refuses_a_database_carrying_an_entry_this_build_does_not() -> None:
    """The asymmetry with `check`: an old migrator pointed at a new database is a deploy-order
    mistake, where an old binary *serving* a new schema is a rolling deploy working."""
    conn = FakeConn([*known_keys("app"), "9999_from_the_future"])

    with pytest.raises(SchemaTooNewError):
        await apply(conn, "app")

    assert conn.executed == []


def test_the_committed_schema_sql_is_what_the_generator_emits() -> None:
    assert SCHEMA_SQL.exists(), f"{SCHEMA_SQL} is missing; run `make schema`"
    assert SCHEMA_SQL.read_text() == emit_sql(DEFAULT_SCHEMA), (
        "deploy/schema.sql does not match app/store/ddl.py. Run `make schema` and commit "
        "the result, the same way openapi.json follows the models."
    )


def test_no_shipped_entry_body_has_changed() -> None:
    """The rule `ddl.py` states, mechanised: never edit an entry that already shipped.

    Fires in the pre-commit hook, before a database exists to be wrong. Why the baseline is
    something a person updates rather than a file `make schema` rewrites is in
    `docs/adr/0003-the-application-never-applies-ddl.md`.
    """
    assert SCHEMA_BASELINE.exists(), f"{SCHEMA_BASELINE} is missing; run `make schema`"
    shipped = json.loads(SCHEMA_BASELINE.read_text())
    current = entry_hashes(DEFAULT_SCHEMA)

    assert set(shipped) == set(current), (
        ".schema-baseline.json does not list the same keys as app/store/ddl.py. Run "
        "`make schema` and commit the result."
    )
    edited = sorted(key for key, was in shipped.items() if current[key] != was)
    assert not edited, (
        f"{edited} shipped, and the body has changed since. A database that already ran it "
        f"never gets the change: `CREATE TABLE IF NOT EXISTS` skips, every key still matches, "
        f"and every query naming what you added fails. Restore the entry and add the change as "
        f"a {ddl.REPAIR_BAND} repair, or hand-edit .schema-baseline.json if the edit is cosmetic."
    )


def test_the_emitted_script_records_every_key() -> None:
    emitted = emit_sql(DEFAULT_SCHEMA)
    for key in known_keys(DEFAULT_SCHEMA):
        assert f"VALUES('{key}')" in emitted, (
            f"{key} is applied by the script and never recorded, so the startup check would "
            f"report a database that ran the whole script as behind."
        )


DESTRUCTIVE = re.compile(
    r"\bDROP\s+TABLE\b|\bDROP\s+COLUMN\b|\bALTER\s+COLUMN\s+\w+\s+TYPE\b|\bRENAME\b",
    re.IGNORECASE,
)


def test_every_entry_is_additive() -> None:
    """The rule that lets `check` serve a database ahead of this build.

    An older instance can only keep running against a newer schema because no migration is
    able to take away or reshape something it writes to. That is a convention until something
    refuses the spelling, and a convention is what a rolling deploy would discover the hard
    way.
    """
    for key, sql in statements(DEFAULT_SCHEMA):
        found = DESTRUCTIVE.search(sql)
        assert found is None, (
            f"{key} contains {found.group(0)!r}. Migrations here are additive: an instance of "
            f"the previous release is still serving while this runs, and app/store/migrate.py "
            f"lets it because nothing can remove what it writes to."
        )


def test_every_created_index_leads_with_the_tenant() -> None:
    """The one real performance constraint of row-level security, mechanised.

    Every query the application makes is filtered by `tenant_id`, so an index that does not
    lead with it cannot be used to satisfy that filter and Postgres falls back to scanning far
    more rows than it should. It fails invisibly -- correct answers, quietly slower -- until
    the table is large enough that fixing it means rebuilding indexes on live data.

    Constraint-backed indexes are exempt and named here rather than skipped silently: a
    primary key on a uuid and the `UNIQUE (id, tenant_id)` a child table's foreign key must
    reference are both about uniqueness of a surrogate key, not about serving a scan.
    """
    for key, sql in statements(DEFAULT_SCHEMA):
        found = re.search(r"CREATE\s+(?:UNIQUE\s+)?INDEX[^(]*\(([^)]*)\)", sql, re.IGNORECASE)
        if found is None:
            continue
        leading = found.group(1).split(",")[0].strip()
        assert leading == "tenant_id", (
            f"{key} indexes ({found.group(1)}) and leads with {leading!r}. Every index on a "
            f"tenant table leads with tenant_id, or the policy cannot use it."
        )


def test_the_committed_roles_sql_is_what_the_generator_emits() -> None:
    assert ROLES_SQL.exists(), f"{ROLES_SQL} is missing; run `make roles`"
    assert ROLES_SQL.read_text() == emit_roles_sql(DEFAULT_SCHEMA), (
        "deploy/roles.sql does not match app/store/roles.py. Run `make roles` and commit it."
    )


def test_the_role_names_derive_from_the_schema() -> None:
    """Roles are cluster-wide. Two projects that hard-coded the same pair would collide the
    moment they shared a cluster, and the second to migrate would inherit the first's grants."""
    assert owner_role("billing") == "billing_owner"
    assert app_role("billing") == "billing_app"
    assert owner_role("app") != owner_role("other")


def test_a_schema_name_too_long_to_derive_a_role_from_is_refused() -> None:
    """Postgres truncates an identifier over 63 bytes rather than refusing it, so two long
    schema names could silently share one role."""
    with pytest.raises(InvalidSchemaName):
        resolve_schema("s" * 57)


EXEMPT_FROM_ISOLATION = frozenset({"applied_once"})
"""Tables that hold no tenant data, named one at a time.

The ledger is the only one: a schema version belongs to the deployment rather than to anybody,
and a policy on it would hide it from the role whose only job is to read it. Adding a name here
is how you say "this is not tenant data" -- out loud, in a diff, rather than by omission.
"""


def created_tables() -> set[str]:
    return {
        name
        for _, sql in statements(DEFAULT_SCHEMA)
        for name in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql)
    }


def test_every_table_is_isolated_or_explicitly_exempt() -> None:
    """The rule that keeps going multi-tenant later from costing anything.

    `TENANT_TABLES` drives both the policy and the `FORCE`, so a table added to `SCHEMA` and
    not to that tuple gets neither -- no error, no failing test, just a table every tenant can
    read. It is the exact failure this design exists to prevent, arriving through the one door
    the design left open, and it would be found by an audit rather than by a build.
    """
    unaccounted = created_tables() - set(ddl.TENANT_TABLES) - EXEMPT_FROM_ISOLATION
    assert not unaccounted, (
        f"{sorted(unaccounted)} is created by SCHEMA but is in neither TENANT_TABLES nor "
        f"EXEMPT_FROM_ISOLATION, so it carries no policy and is readable across every "
        f"tenant. Add it to one or the other."
    )


def test_every_isolated_table_actually_exists() -> None:
    """The mirror. A name in `TENANT_TABLES` with no table behind it emits a policy for
    something that is not there, and fails on a real server rather than here."""
    missing = set(ddl.TENANT_TABLES) - created_tables()
    assert not missing, f"{sorted(missing)} is in TENANT_TABLES and created by nothing"


def test_every_tenant_table_carries_the_column_and_the_constraint() -> None:
    """A table can be in `TENANT_TABLES` and still be wrong.

    Without the column the policy fails at migration time, which is loud and fine. The `CHECK`
    is the quiet one: without it a row can carry an empty tenant, and an empty tenant is what
    every connection that never set one compares against -- a cross-tenant read produced by a
    data bug, which no policy prevents.
    """
    for table in ddl.TENANT_TABLES:
        create = next(
            (
                s
                for _, s in statements(DEFAULT_SCHEMA)
                if f"CREATE TABLE IF NOT EXISTS {table}" in s
            ),
            None,
        )
        assert create is not None, table
        assert "tenant_id" in create, f"{table} is in TENANT_TABLES and has no tenant_id column"
        assert "tenant_id <> ''" in create, (
            f"{table} may store an empty tenant_id, which every connection that set no tenant "
            f"can read. Add CHECK (tenant_id <> '')."
        )
