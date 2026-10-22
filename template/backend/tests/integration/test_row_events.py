"""The row event: every write to a tenant table records one, in the transaction that wrote it.

Postgres keeps this with `audit_row_change` and a trigger on every table in `TENANT_TABLES`. The
in-memory substrate keeps none of it. Every test here writes `tasks` as SQL, or a table of its
own, and never through the example's store. `docs/adr/template/0017`.
"""

from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest
from asgi_correlation_id import correlation_id

from app.audit_events import RequestEvent, acting_as
from app.identity.shared import Actor
from app.store import Database
from app.store.acting import MIGRATION_ACTOR
from app.store.ddl import AUDITED_WRITES, TENANT_GUC, TENANT_TABLES, audit_sql
from app.store.migrate import apply
from app.store.roles import APPEND_ONLY_TABLE
from tests.integration.conftest import (
    THE_SUITE,
    Provisioned,
    acting_on,
    connect,
)

ACME = "acme"
GLOBEX = "globex"
FIRST = "00000000-0000-0000-0000-000000000001"
SECOND = "00000000-0000-0000-0000-000000000002"

PERSON = Actor(issuer="a-test", subject="a-person", name="A person")
AGENT = Actor(issuer="tasks", subject="an-agent", name="An agent", on_behalf_of=PERSON)

_INSERT = "INSERT INTO tasks (id, tenant_id, title) VALUES ($1, $2, $3)"
_EVENTS = (
    "SELECT tenant_id, actor_issuer, actor_subject, actor_name, on_behalf_of_subject, action, "
    "outcome, resource_type, method, route, status, resource_ids, fields, request_id, trace_id "
    "FROM audit_events ORDER BY seq"
)


async def an_app_connection(provisioned: Provisioned) -> Any:
    conn: Any = await connect(provisioned.app_dsn)
    await conn.execute(f'SET search_path TO "{provisioned.schema}"')
    return conn


async def in_tenant(conn: Any, tenant: str, statement: str, *args: Any) -> Any:
    async with conn.transaction():
        await conn.execute("SELECT set_config($1, $2, true)", TENANT_GUC, tenant)
        return await conn.fetch(statement, *args)


@pytest.fixture
async def app_conn(provisioned: Provisioned) -> AsyncIterator[Any]:
    conn = await an_app_connection(provisioned)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def admin(provisioned: Provisioned) -> AsyncIterator[Any]:
    """A superuser connection on the schema. It bypasses every policy, so it reads and writes
    across tenants."""
    conn: Any = await connect(provisioned.admin_dsn)
    await conn.execute(f'SET search_path TO "{provisioned.schema}"')
    try:
        yield conn
    finally:
        await conn.close()


async def test_a_write_with_no_actor_is_refused_and_changes_nothing(
    app_conn: Any, admin: Any
) -> None:
    with pytest.raises(asyncpg.RaiseError, match="a write to tasks names no actor"):
        await in_tenant(app_conn, ACME, _INSERT, FIRST, ACME, "nobody wrote this")

    assert await admin.fetchval("SELECT count(*) FROM tasks") == 0
    assert await admin.fetchval("SELECT count(*) FROM audit_events") == 0


async def test_each_write_records_who_did_what_to_which_row(app_conn: Any, admin: Any) -> None:
    await acting_on(app_conn)

    await in_tenant(app_conn, ACME, _INSERT, FIRST, ACME, "audit me")
    await in_tenant(app_conn, ACME, "UPDATE tasks SET done = true WHERE id = $1", FIRST)
    await in_tenant(app_conn, ACME, "DELETE FROM tasks WHERE id = $1", FIRST)

    rows = await admin.fetch(_EVENTS)
    assert [
        (r["tenant_id"], r["action"], r["outcome"], r["resource_type"], r["resource_ids"])
        for r in rows
    ] == [
        (ACME, "create", "done", "tasks", [FIRST]),
        (ACME, "update", "done", "tasks", [FIRST]),
        (ACME, "delete", "done", "tasks", [FIRST]),
    ]
    assert [r["fields"] for r in rows][1] == ["done"]
    assert {(r["actor_issuer"], r["actor_subject"], r["actor_name"]) for r in rows} == {
        (THE_SUITE.issuer, THE_SUITE.subject, THE_SUITE.name)
    }
    assert {
        (r["method"], r["route"], r["status"], r["request_id"], r["trace_id"]) for r in rows
    } == {(None, None, None, None, None)}


async def test_an_insert_and_a_delete_name_every_column(app_conn: Any, admin: Any) -> None:
    await acting_on(app_conn)
    columns = sorted(
        await admin.fetchval(
            "SELECT array_agg(attname::text) FROM pg_attribute "
            "WHERE attrelid = 'tasks'::regclass AND attnum > 0 AND NOT attisdropped"
        )
    )

    await in_tenant(app_conn, ACME, _INSERT, FIRST, ACME, "whole")
    await in_tenant(app_conn, ACME, "DELETE FROM tasks")

    assert [r["fields"] for r in await admin.fetch(_EVENTS)] == [columns, columns]


async def test_one_statement_records_one_event_for_each_tenant_it_wrote(admin: Any) -> None:
    await acting_on(admin)

    await admin.execute(
        "INSERT INTO tasks (id, tenant_id, title) "
        "VALUES ($1, $2, 'a'), ($3, $4, 'b'), ($5, $2, 'c')",
        FIRST,
        ACME,
        SECOND,
        GLOBEX,
        "00000000-0000-0000-0000-000000000003",
    )

    assert [(r["tenant_id"], r["resource_ids"]) for r in await admin.fetch(_EVENTS)] == [
        (ACME, [FIRST, "00000000-0000-0000-0000-000000000003"]),
        (GLOBEX, [SECOND]),
    ]


async def test_a_statement_that_writes_no_row_records_nothing(app_conn: Any, admin: Any) -> None:
    await acting_on(app_conn)

    await in_tenant(app_conn, ACME, "UPDATE tasks SET done = true WHERE id = $1", FIRST)

    assert await admin.fetchval("SELECT count(*) FROM audit_events") == 0


async def test_an_update_names_only_the_columns_it_changed(app_conn: Any, admin: Any) -> None:
    await acting_on(app_conn)
    await in_tenant(app_conn, ACME, _INSERT, FIRST, ACME, "same")

    await in_tenant(app_conn, ACME, "UPDATE tasks SET title = 'same', done = true")

    assert (await admin.fetch(_EVENTS))[-1]["fields"] == ["done"]


async def test_an_event_holds_no_value_a_row_holds(app_conn: Any, admin: Any) -> None:
    await acting_on(app_conn)

    await in_tenant(app_conn, ACME, _INSERT, FIRST, ACME, "a value only the row may hold")

    assert "a value only the row may hold" not in repr(
        await admin.fetch("SELECT * FROM audit_events")
    )


async def test_an_app_acting_for_a_person_is_recorded_with_the_person(
    app_conn: Any, admin: Any
) -> None:
    await acting_on(app_conn, AGENT)

    await in_tenant(app_conn, ACME, _INSERT, FIRST, ACME, "drafted by an agent")

    [row] = await admin.fetch(_EVENTS)
    assert (row["actor_subject"], row["on_behalf_of_subject"]) == ("an-agent", "a-person")


async def test_a_write_whose_event_cannot_be_recorded_changes_nothing(
    provisioned: Provisioned, app_conn: Any, admin: Any
) -> None:
    """The row and its event are one transaction."""
    await acting_on(app_conn)
    await admin.execute(f'REVOKE INSERT ON audit_events FROM "{provisioned.app}"')

    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="audit_events"):
        await in_tenant(app_conn, ACME, _INSERT, FIRST, ACME, "lost")

    assert await admin.fetchval("SELECT count(*) FROM tasks") == 0


async def test_a_composite_key_names_a_row_by_each_key_column_in_order(admin: Any) -> None:
    await admin.execute(
        "CREATE TABLE chunks (tenant_id text NOT NULL, blob uuid, seq int, "
        "PRIMARY KEY (tenant_id, blob, seq))"
    )
    await admin.execute(audit_sql("chunks"))
    await acting_on(admin)

    await admin.execute("INSERT INTO chunks VALUES ($1, $2, 7)", ACME, FIRST)

    assert (await admin.fetch(_EVENTS))[-1]["resource_ids"] == [f"{ACME}:{FIRST}:7"]


async def test_a_table_with_no_primary_key_cannot_be_written(admin: Any) -> None:
    await admin.execute("CREATE TABLE keyless (tenant_id text NOT NULL, note text)")
    await admin.execute(audit_sql("keyless"))
    await acting_on(admin)

    with pytest.raises(asyncpg.RaiseError, match="keyless has no primary key"):
        await admin.execute("INSERT INTO keyless VALUES ($1, 'unnamed')", ACME)


async def test_every_tenant_table_but_the_audit_events_carries_its_triggers(admin: Any) -> None:
    rows = await admin.fetch(
        "SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
        "WHERE c.relnamespace = current_schema()::regnamespace AND NOT t.tgisinternal"
    )
    found = {(r["relname"], r["tgname"]) for r in rows}

    assert found == {
        (table, f"{table}_audit_{write}")
        for table in TENANT_TABLES
        if table != APPEND_ONLY_TABLE
        for write, _ in AUDITED_WRITES
    }


async def test_a_row_a_migration_writes_is_recorded_under_the_release_step(
    provisioned: Provisioned, admin: Any
) -> None:
    """An entry that writes a row runs on the connection `apply` used, as the owner role."""
    conn: Any = await connect(provisioned.admin_dsn)
    try:
        await apply(conn, provisioned.schema)
        await in_tenant(conn, ACME, _INSERT, FIRST, ACME, "written by an entry")
    finally:
        await conn.close()

    [row] = await admin.fetch(_EVENTS)
    assert (row["actor_issuer"], row["actor_subject"], row["action"]) == (
        MIGRATION_ACTOR.issuer,
        MIGRATION_ACTOR.subject,
        "create",
    )


@pytest.fixture
async def one_connection(provisioned: Provisioned) -> AsyncIterator[Database]:
    """A `PostgresDatabase` whose pool holds one connection."""
    from app.store.pg import PostgresDatabase

    database = PostgresDatabase(
        dsn=provisioned.app_dsn, schema=provisioned.schema, min_size=1, max_size=1
    )
    await database.check()
    try:
        yield database
    finally:
        await database.close()


async def written_through(database: Any, title: str) -> None:
    async with database.scoped(ACME) as conn:
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, title) VALUES (gen_random_uuid(), $1, $2)",
            ACME,
            title,
        )


async def test_the_pool_names_who_acts_and_in_which_request(
    one_connection: Any, admin: Any
) -> None:
    token = correlation_id.set("a-request")
    try:
        with acting_as(AGENT):
            await written_through(one_connection, "in a request")
    finally:
        correlation_id.reset(token)

    [row] = await admin.fetch(_EVENTS)
    assert (row["actor_subject"], row["on_behalf_of_subject"], row["request_id"]) == (
        "an-agent",
        "a-person",
        "a-request",
    )


async def test_a_connection_lent_again_names_nobody(one_connection: Any) -> None:
    """The same connection, lent again, does not carry the actor it had."""
    with acting_as(PERSON):
        await written_through(one_connection, "acted")

    with pytest.raises(asyncpg.RaiseError, match="names no actor"):
        await written_through(one_connection, "nobody")


async def test_a_database_from_before_request_events_is_repaired(
    provisioned: Provisioned, admin: Any, one_connection: Any
) -> None:
    """`audit_events` as 0.41.0 left it, with an event in it, takes a request event after the
    next `make migrate`, and keeps the event it held."""
    await admin.execute(
        "ALTER TABLE audit_events DROP CONSTRAINT audit_events_kind_whole, "
        "DROP COLUMN method, DROP COLUMN route, DROP COLUMN status, "
        "ALTER COLUMN action SET NOT NULL, ALTER COLUMN outcome SET NOT NULL, "
        "ALTER COLUMN outcome SET DEFAULT 'done', ALTER COLUMN resource_type SET NOT NULL"
    )
    await admin.execute(
        "INSERT INTO audit_events (tenant_id, actor_issuer, actor_subject, actor_name, action, "
        "outcome, resource_type, resource_ids, fields) "
        "VALUES ($1, 'a-test', 'a-person', 'A person', 'update', 'absent', 'tasks', '{x}', '{}')",
        ACME,
    )
    conn: Any = await connect(provisioned.admin_dsn)
    try:
        await apply(conn, provisioned.schema)
    finally:
        await conn.close()

    events = one_connection.audit_events(ACME)
    await events.record(
        RequestEvent(
            actor=PERSON,
            method="GET",
            route="/tasks",
            resource_ids=(),
            status=200,
            request_id=None,
            trace_id=None,
        )
    )

    assert [(e.action, e.outcome, e.method, e.route) for e in await events.list()] == [
        ("update", "absent", None, None),
        (None, None, "GET", "/tasks"),
    ]
    await assert_an_event_of_both_kinds_is_refused(admin)


async def assert_an_event_of_both_kinds_is_refused(admin: Any) -> None:
    with pytest.raises(asyncpg.CheckViolationError, match="audit_events_kind_whole"):
        await admin.execute(
            "INSERT INTO audit_events (tenant_id, actor_issuer, actor_subject, actor_name, action, "
            "outcome, resource_type, method, route, status, resource_ids, fields) VALUES "
            "($1, 'a-test', 'a-person', 'A person', 'read', 'done', 'tasks', 'GET', '/tasks', 200, "
            "'{}', '{}')",
            ACME,
        )


async def test_an_event_of_both_kinds_is_refused(admin: Any) -> None:
    await assert_an_event_of_both_kinds_is_refused(admin)
