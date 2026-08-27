"""Tenant isolation, against a real server, as the roles a deployment actually uses.

Nothing here runs as a superuser, and that is not incidental: **a superuser bypasses row-level
security regardless of `FORCE`**, so a suite that asserted isolation from the admin connection
would pass against a schema with no policies at all. Every assertion below is made as
`<schema>_app` or after `SET ROLE <schema>_owner`.

Each test gets its own schema and its own pair of roles, named after it. Roles are
cluster-wide, so a fixed pair would make this file unable to run beside itself.

`docs/adr/0002` holds the decision these assertions enforce and the options it rejected.
"""

from typing import Any

import asyncpg
import pytest

from app.store.ddl import TENANT_GUC, TENANT_TABLES
from app.store.migrate import SchemaBehindError, apply, check
from tests.integration.conftest import (
    Provisioned,
    a_fresh_schema_name,
    connect,
    drop_schema,
    postgres_dsn,
)

ACME = "acme"
GLOBEX = "globex"

_INSERT = "INSERT INTO tasks (id, tenant_id, title) VALUES (gen_random_uuid(), $1, $2)"
_COUNT = "SELECT count(*) FROM tasks"


async def seeded(provisioned: Provisioned) -> Any:
    """An application-role connection with one row for each of two tenants."""
    conn: Any = await connect(provisioned.app_dsn)
    await conn.execute(f'SET search_path TO "{provisioned.schema}"')
    for tenant in (ACME, GLOBEX):
        async with conn.transaction():
            await conn.execute("SELECT set_config($1, $2, true)", TENANT_GUC, tenant)
            await conn.execute(_INSERT, tenant, f"{tenant} task")
    return conn


async def as_owner(provisioned: Provisioned) -> Any:
    """An admin connection that has become the owning role, which `FORCE` still binds."""
    conn: Any = await connect(provisioned.admin_dsn)
    await conn.execute(f'SET ROLE "{provisioned.owner}"')
    await conn.execute(f'SET search_path TO "{provisioned.schema}"')
    return conn


async def test_the_owner_is_subject_to_its_own_policy(provisioned: Provisioned) -> None:
    """The one that proves `FORCE`.

    A table's owner bypasses its own policies by default, so with `ENABLE` alone this reads 2
    -- silently, with nothing anywhere reporting a problem. Verified both ways against
    PostgreSQL 17: dropping `FORCE` from `_force_sql` makes exactly this line fail.
    """
    app = await seeded(provisioned)
    owner = await as_owner(provisioned)
    try:
        assert await owner.fetchval(_COUNT) == 0
    finally:
        await app.close()
        await owner.close()


async def test_force_is_set_on_the_tenant_table_and_not_on_the_ledger(
    provisioned: Provisioned,
) -> None:
    conn: Any = await connect(provisioned.admin_dsn)
    try:
        rows = await conn.fetch(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = $1 AND c.relkind = 'r'",
            provisioned.schema,
        )
        state = {r["relname"]: (r["relrowsecurity"], r["relforcerowsecurity"]) for r in rows}
        for table in TENANT_TABLES:
            assert state[table] == (True, True), table
        assert state["applied_once"] == (False, False), "the ledger is not tenant data"
    finally:
        await conn.close()


async def test_a_set_tenant_reads_exactly_its_own_rows(provisioned: Provisioned) -> None:
    conn = await seeded(provisioned)
    try:
        for tenant in (ACME, GLOBEX):
            async with conn.transaction():
                await conn.execute("SELECT set_config($1, $2, true)", TENANT_GUC, tenant)
                assert await conn.fetchval(_COUNT) == 1
    finally:
        await conn.close()


async def test_an_empty_tenant_is_not_a_wildcard(provisioned: Provisioned) -> None:
    """`RESET ALL` on pool release leaves the setting empty, not absent. Both match nothing."""
    conn = await seeded(provisioned)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config($1, $2, true)", TENANT_GUC, "")
            assert await conn.fetchval(_COUNT) == 0
    finally:
        await conn.close()


async def test_an_empty_tenant_cannot_be_stored(provisioned: Provisioned) -> None:
    """The constraint that makes "an empty setting matches nothing" true rather than lucky.

    Without `tasks_tenant_id_not_empty`, one row carrying an empty tenant would be readable by
    every connection that had not set one -- a permanent cross-tenant read caused by a data
    bug rather than a policy bug.
    """
    conn = await seeded(provisioned)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config($1, $2, true)", TENANT_GUC, "")
            with pytest.raises(asyncpg.IntegrityConstraintViolationError):
                await conn.execute(_INSERT, "", "invisible to nobody")
    finally:
        await conn.close()


async def test_the_tenant_does_not_survive_its_transaction(provisioned: Provisioned) -> None:
    """`SET LOCAL` is reverted by the server at commit, before any pooler can reassign."""
    conn = await seeded(provisioned)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config($1, $2, true)", TENANT_GUC, ACME)
            assert await conn.fetchval(_COUNT) == 1
        assert await conn.fetchval(_COUNT) == 0
    finally:
        await conn.close()


async def test_a_tenant_cannot_write_a_row_it_could_not_read(provisioned: Provisioned) -> None:
    """The property, not the clause. `FOR ALL` with `USING` checks new rows against `USING`
    too, so this passes with `WITH CHECK` removed -- which is the right behaviour to assert:
    it holds whichever clause is doing the work."""
    conn = await seeded(provisioned)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config($1, $2, true)", TENANT_GUC, ACME)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(_INSERT, GLOBEX, "smuggled")
    finally:
        await conn.close()


async def test_a_tenant_cannot_delete_another_tenants_rows(provisioned: Provisioned) -> None:
    conn = await seeded(provisioned)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config($1, $2, true)", TENANT_GUC, ACME)
            await conn.execute("DELETE FROM tasks")
        async with conn.transaction():
            await conn.execute("SELECT set_config($1, $2, true)", TENANT_GUC, GLOBEX)
            assert await conn.fetchval(_COUNT) == 1
    finally:
        await conn.close()


async def test_the_application_role_cannot_create_tables(provisioned: Provisioned) -> None:
    """What makes `check` mean something: this role could not apply the schema if it tried."""
    conn: Any = await connect(provisioned.app_dsn)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(f'CREATE TABLE "{provisioned.schema}".nope (i int)')
    finally:
        await conn.close()


async def test_the_application_role_holds_no_bypass(provisioned: Provisioned) -> None:
    """`BYPASSRLS` is a role attribute: it applies to every statement the role runs, and
    setting a tenant does not re-scope it. A login role holding it is silently unisolated."""
    conn: Any = await connect(provisioned.admin_dsn)
    try:
        for role in (provisioned.app, provisioned.owner):
            attributes = await conn.fetchrow(
                "SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = $1", role
            )
            assert attributes["rolbypassrls"] is False, role
            assert attributes["rolsuper"] is False, role
    finally:
        await conn.close()


async def test_no_security_definer_function_exists_in_the_schema(
    provisioned: Provisioned,
) -> None:
    """Nothing here can see across tenants -- mechanised.

    A `SECURITY DEFINER` function is how a bypass is normally reached, and this project has no
    question that needs one -- no queue taking the next row across tenants, nothing that must
    look before it knows whose data it is. If one ever appears, it should have to argue for
    itself in a diff rather than arriving quietly.
    """
    conn: Any = await connect(provisioned.admin_dsn)
    try:
        found = await conn.fetch(
            "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = $1 AND p.prosecdef",
            provisioned.schema,
        )
        assert [r["proname"] for r in found] == []
    finally:
        await conn.close()


async def test_the_application_role_cannot_rewrite_the_ledger(provisioned: Provisioned) -> None:
    """The role that verifies the schema must not be able to forge its own answer."""
    conn: Any = await connect(provisioned.app_dsn)
    try:
        await conn.execute(f'SET search_path TO "{provisioned.schema}"')
        assert await conn.fetchval("SELECT count(*) FROM applied_once") > 0
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("INSERT INTO applied_once(key) VALUES ('9999_forged')")
    finally:
        await conn.close()


async def test_the_migration_leaves_every_object_owned_by_the_owner_role(
    provisioned: Provisioned,
) -> None:
    """Objects created without `SET ROLE` fall outside the owner's default privileges, and
    the application is then refused at query time rather than at migration time."""
    conn: Any = await connect(provisioned.admin_dsn)
    try:
        rows = await conn.fetch(
            "SELECT c.relname, pg_get_userbyid(c.relowner) AS owner FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = $1 AND c.relkind IN ('r', 'S')",
            provisioned.schema,
        )
        assert {r["owner"] for r in rows} == {provisioned.owner}
    finally:
        await conn.close()


async def test_check_is_enough_for_the_application_role(provisioned: Provisioned) -> None:
    """The whole point of the split: this role may verify and may not apply."""
    conn: Any = await connect(provisioned.app_dsn)
    try:
        assert await check(conn, provisioned.schema) is not None
    finally:
        await conn.close()


async def test_an_ordinary_role_can_migrate_a_fresh_database() -> None:
    """The bootstrap. A developer's own Postgres has no roles provisioned, and must still work.

    `_become_owner` finds no such role and carries on as whoever connected. `FORCE` keeps the
    objects policy-bound regardless of who owns them, which is why carrying on is safe.
    """
    dsn = postgres_dsn()
    schema = a_fresh_schema_name()
    try:
        assert await apply_and_check(dsn, schema) is not None
    finally:
        await drop_schema(dsn, schema)


async def apply_and_check(dsn: str, schema: str) -> str | None:
    conn: Any = await connect(dsn)
    try:
        await apply(conn, schema)
        return await check(conn, schema)
    finally:
        await conn.close()


async def test_check_refuses_a_database_that_was_never_migrated() -> None:
    dsn = postgres_dsn()
    schema = a_fresh_schema_name()
    conn: Any = await connect(dsn)
    try:
        with pytest.raises(SchemaBehindError):
            await check(conn, schema)
    finally:
        await conn.close()
