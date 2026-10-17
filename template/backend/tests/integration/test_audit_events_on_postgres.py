"""Audit events against a real server, as the roles a deployment uses: what the application role
may not do to one, and what happens to a write whose event it cannot record.

`tests/store_contract.py` holds what both substrates promise. These hold what only Postgres can
show, because only Postgres has grants. `docs/adr/template/0017`.
"""

from typing import Any

import asyncpg
import pytest

from app.identity.shared import SENTINEL_TENANT, Actor
from app.models import CreateTaskBody
from app.store.ddl import TENANT_GUC
from app.store.roles import emit_roles_sql
from tests.integration.conftest import Provisioned, a_postgres_database, connect

ACTOR = Actor(issuer="a-test", subject="the-suite", name="The suite")

_FORBIDDEN = (
    "UPDATE audit_events SET actor_subject = 'somebody else'",
    "DELETE FROM audit_events",
)


async def as_admin(provisioned: Provisioned) -> Any:
    conn: Any = await connect(provisioned.admin_dsn)
    await conn.execute(f'SET search_path TO "{provisioned.schema}"')
    return conn


async def assert_the_app_role_cannot_update_or_delete_an_event(provisioned: Provisioned) -> None:
    app: Any = await connect(provisioned.app_dsn)
    await app.execute(f'SET search_path TO "{provisioned.schema}"')
    try:
        for statement in _FORBIDDEN:
            async with app.transaction():
                await app.execute("SELECT set_config($1, $2, true)", TENANT_GUC, SENTINEL_TENANT)
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await app.execute(statement)
    finally:
        await app.close()


async def test_the_application_role_cannot_update_or_delete_an_event(
    provisioned: Provisioned,
) -> None:
    database = await a_postgres_database(provisioned)
    try:
        await database.store(SENTINEL_TENANT).create(ACTOR, CreateTaskBody(title="recorded"))
    finally:
        await database.close()

    await assert_the_app_role_cannot_update_or_delete_an_event(provisioned)


async def test_applying_the_roles_again_does_not_grant_update_or_delete_again(
    provisioned: Provisioned,
) -> None:
    """`deploy/roles.sql` grants every write on every table that exists. Applied again after a
    migration, it revokes `UPDATE` and `DELETE` on the events again in the same script."""
    admin: Any = await connect(provisioned.admin_dsn)
    try:
        await admin.execute(emit_roles_sql(provisioned.schema))
    finally:
        await admin.close()

    await assert_the_app_role_cannot_update_or_delete_an_event(provisioned)


async def test_a_write_whose_event_cannot_be_recorded_changes_nothing(
    provisioned: Provisioned,
) -> None:
    """The row and its event are one transaction. With the insert on `audit_events` refused, the
    create fails, and the task it would have written is not there."""
    admin = await as_admin(provisioned)
    database = await a_postgres_database(provisioned)
    try:
        await admin.execute(f'REVOKE INSERT ON audit_events FROM "{provisioned.app}"')

        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="audit_events"):
            await database.store(SENTINEL_TENANT).create(ACTOR, CreateTaskBody(title="lost"))

        await admin.execute(f'GRANT INSERT ON audit_events TO "{provisioned.app}"')
        assert await database.store(SENTINEL_TENANT).list(ACTOR) == []
    finally:
        await database.close()
        await admin.close()
