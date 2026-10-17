"""What happens on a real server to a write whose audit event the application role cannot record.

`tests/store_contract.py` holds what both substrates promise, and `tests/integration/
test_isolation.py` what the application role may never write. This holds the one rule only a
real transaction can show, on the example's store. `docs/adr/template/0017`.
"""

from typing import Any

import asyncpg
import pytest

from app.identity.shared import SENTINEL_TENANT, Actor
from app.models import CreateTaskBody
from tests.integration.conftest import Provisioned, a_postgres_database, connect

ACTOR = Actor(issuer="a-test", subject="the-suite", name="The suite")


async def test_a_write_whose_event_cannot_be_recorded_changes_nothing(
    provisioned: Provisioned,
) -> None:
    """The row and its event are one transaction. With the insert on `audit_events` refused, the
    create fails, and the task it would have written is not there."""
    admin: Any = await connect(provisioned.admin_dsn)
    await admin.execute(f'SET search_path TO "{provisioned.schema}"')
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
