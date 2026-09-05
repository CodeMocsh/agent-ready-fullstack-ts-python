"""What only a real server can answer.

`tests/store/test_schema.py` covers everything about the schema that a fake connection can drive,
so what is left here is the part that needs Postgres to actually parse and apply the DDL:
that it applies, that re-applying is a no-op, that `check` reads a real ledger, that a
database missing a later column gets repaired, and that the whole stack -- HTTP, the wiring,
the pool, the SQL -- serves a task round trip.

The application never applies anything here, because it cannot: every setup below goes
through `conftest.apply_schema`, which is a separate privileged connection, exactly as
`make migrate` is.

Every test takes a schema of its own and drops it afterwards. Run the suite with
`make db-test`, which supplies the server and the `TEST_DATABASE_URL` pointing at it.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.store import Database, ddl
from app.store.migrate import emit_sql, known_version
from tests.integration.conftest import (
    Provisioned,
    a_fresh_schema_name,
    apply_schema,
    connect,
    drop_schema,
    postgres_dsn,
)

NOTE_REPAIR = (
    f"{ddl.REPAIR_BAND}tasks_note",
    "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS note text",
)
"""A repair entry injected by one test. The band has no real occupant yet, and the failure it
guards against -- a column added to a shipped `CREATE TABLE IF NOT EXISTS`, which reaches no
database that already had the table -- bites on the first schema change rather than later."""


def a_database(schema: str) -> Any:
    from app.store.pg import PostgresDatabase

    return PostgresDatabase(dsn=postgres_dsn(), schema=schema)


async def test_the_schema_applies_and_reports_its_version(database: Database) -> None:
    assert await database.schema_version() == known_version()


async def test_applying_twice_changes_nothing() -> None:
    """`make migrate` wired into a release hook may fire more than once. The second run
    re-applies every entry, which changes nothing because each one is idempotent, and exits
    cleanly rather than raising an error the pipeline has to be taught to ignore."""
    schema = a_fresh_schema_name()
    try:
        assert await apply_schema(postgres_dsn(), schema) == known_version()
        assert await apply_schema(postgres_dsn(), schema) == known_version()
    finally:
        await drop_schema(postgres_dsn(), schema)


async def test_check_passes_once_the_release_step_has_applied_the_schema() -> None:
    schema = a_fresh_schema_name()
    verifier = a_database(schema)
    try:
        await apply_schema(postgres_dsn(), schema)
        assert await verifier.check() == known_version()
    finally:
        await verifier.close()
        await drop_schema(postgres_dsn(), schema)


async def test_a_database_missing_a_later_column_is_repaired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = a_fresh_schema_name()
    database = a_database(schema)
    try:
        await apply_schema(postgres_dsn(), schema)
        pool = await database.pool()
        assert await _column_exists(pool, schema, "note") is False

        monkeypatch.setattr(ddl, "SCHEMA", [*ddl.SCHEMA, NOTE_REPAIR])
        assert await apply_schema(postgres_dsn(), schema) == NOTE_REPAIR[0]

        assert await _column_exists(pool, schema, "note") is True
    finally:
        await database.close()
        await drop_schema(postgres_dsn(), schema)


async def test_the_pool_points_at_our_schema_on_every_checkout() -> None:
    """The `RESET ALL` trap. A `SET search_path` from a connect hook survives one checkout;
    a startup parameter survives all of them. Held over enough checkouts to exhaust the pool
    and come back round, because the first query on each connection works either way."""
    schema = a_fresh_schema_name()
    database = a_database(schema)
    try:
        await apply_schema(postgres_dsn(), schema)
        pool = await database.pool()
        for _ in range(6):
            assert await pool.fetchval("SELECT current_schema()") == schema
    finally:
        await database.close()
        await drop_schema(postgres_dsn(), schema)


async def test_the_emitted_script_applies_and_satisfies_check_mode() -> None:
    """`deploy/schema.sql` run by hand, then the app verifying it.

    The whole promise of the emitted script is that a database whose own tooling applied it
    passes the startup check afterwards -- which needs every statement to be in an order
    Postgres accepts *and* every key recorded. Asserting the text alone cannot see either:
    the first version of `emit_sql` wrote the ledger's first row above the statement that
    creates the ledger, and every text assertion passed.
    """
    schema = a_fresh_schema_name()
    conn: Any = await connect(postgres_dsn())
    verifier = a_database(schema)
    try:
        await conn.execute(emit_sql(schema))
        assert await verifier.check() == known_version()
    finally:
        await conn.close()
        await verifier.close()
        await drop_schema(postgres_dsn(), schema)


async def _column_exists(pool: Any, schema: str, column: str) -> bool:
    found = await pool.fetchval(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = $1 AND table_name = 'tasks' AND column_name = $2",
        schema,
        column,
    )
    return found is not None


def test_the_whole_stack_serves_a_task_round_trip(
    provisioned: Provisioned, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HTTP through the wiring, the pool and real SQL, as the role a deployment runs as.

    Sync rather than async, and deliberately: `TestClient` drives the app's lifespan on its
    own event loop, so this is the one suite that exercises `create_app()` the way uvicorn
    does -- including the schema check that runs before the first request, against a schema
    something else applied.
    """
    monkeypatch.setenv("DATABASE_URL", provisioned.app_dsn)
    monkeypatch.setenv("DB_SCHEMA", provisioned.schema)

    with TestClient(create_app()) as client:
        assert client.get("/tasks").json() == []

        created = client.post("/tasks", json={"title": "Round trip"})
        assert created.status_code == 201
        task = created.json()
        assert task["done"] is False

        assert [row["id"] for row in client.get("/tasks").json()] == [task["id"]]

        patched = client.patch(f"/tasks/{task['id']}", json={"done": True})
        assert patched.status_code == 200
        assert patched.json()["done"] is True

        assert client.patch("/tasks/not-a-uuid", json={"done": True}).status_code == 404
        assert client.delete("/tasks/not-a-uuid").status_code == 404

        assert client.delete(f"/tasks/{task['id']}").status_code == 204
        assert client.get("/tasks").json() == []


def test_the_app_reports_postgres_as_its_substrate(
    provisioned: Provisioned, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guards the silent-fallback failure: a deployment that came up on memory would pass
    every test above and lose its data on restart."""
    monkeypatch.setenv("DATABASE_URL", provisioned.app_dsn)
    monkeypatch.setenv("DB_SCHEMA", provisioned.schema)

    app = create_app()
    with TestClient(app):
        assert app.state.database.name == "postgres"
