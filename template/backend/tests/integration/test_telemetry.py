"""What a real Postgres produces: database spans named by operation, with no query text and no
value that was sent, only inside a request, and the pool's connections as metrics."""

import json
from collections.abc import Callable

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, NumberDataPoint
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from app import telemetry
from app.deps import database_of
from app.main import create_app
from app.store.pg import PostgresDatabase
from tests.doubles import CANARY, telemetry_settings
from tests.integration.conftest import Provisioned
from tests.widgets import Widget


def sending_a_value_to_postgres(app: FastAPI) -> FastAPI:
    """`app`, also serving `POST /sent`, which hands the body's name to Postgres as a parameter."""

    @app.post("/sent")
    async def sent(request: Request, body: Widget) -> None:
        database = database_of(request)
        assert isinstance(database, PostgresDatabase)
        async with database.connection() as conn:
            await conn.fetchval("SELECT $1::text", body.name)

    return app


def exported_by(
    provisioned: Provisioned, monkeypatch: pytest.MonkeyPatch, drive: Callable[[TestClient], None]
) -> tuple[ReadableSpan, ...]:
    """The spans an instrumented app on Postgres exports while `drive` uses it."""
    monkeypatch.setenv("DATABASE_URL", provisioned.app_dsn)
    monkeypatch.setenv("DB_SCHEMA", provisioned.schema)
    app = sending_a_value_to_postgres(create_app())
    spans = InMemorySpanExporter()
    instruments = telemetry.instrument(app, telemetry_settings(), spans, InMemoryMetricReader())
    try:
        with TestClient(app) as client:
            drive(client)
        instruments.tracer_provider.force_flush()
    finally:
        instruments.shutdown()
    return spans.get_finished_spans()


def test_a_query_is_a_span_named_by_its_operation_and_carrying_no_value(
    provisioned: Provisioned, monkeypatch: pytest.MonkeyPatch
) -> None:
    def sends(client: TestClient) -> None:
        assert client.post("/sent", json={"name": CANARY}).status_code == 200

    exported = exported_by(provisioned, monkeypatch, sends)

    queries = [one for one in exported if one.kind is SpanKind.CLIENT]
    assert queries
    for query in queries:
        assert query.attributes is not None
        assert query.attributes["db.system.name"] == "postgresql"
        assert "db.query.text" not in query.attributes
        assert set(query.attributes) <= telemetry.SPAN_ATTRIBUTES
    assert CANARY not in json.dumps([(one.name, dict(one.attributes or {})) for one in exported])


def test_a_query_exports_only_inside_a_traced_request(
    provisioned: Provisioned, monkeypatch: pytest.MonkeyPatch
) -> None:
    def queries_outside_and_inside_a_request(client: TestClient) -> None:
        assert client.portal is not None
        client.portal.call(outside_any_request, client.app)
        assert client.get("/ready").status_code == 200
        assert client.post("/sent", json={"name": "sent"}).status_code == 200

    exported = exported_by(provisioned, monkeypatch, queries_outside_and_inside_a_request)

    [server] = [one for one in exported if one.kind is SpanKind.SERVER]
    queries = [one for one in exported if one.kind is SpanKind.CLIENT]
    assert server.context is not None
    assert queries
    assert {one.context.trace_id for one in exported if one.context is not None} == {
        server.context.trace_id
    }
    for query in queries:
        assert query.parent is not None
        assert query.parent.span_id == server.context.span_id


async def outside_any_request(app: FastAPI) -> None:
    database = app.state.database
    assert isinstance(database, PostgresDatabase)
    async with database.connection() as conn:
        await conn.fetchval("SELECT 1")


def test_the_pool_reports_its_connections_by_state_and_the_most_it_will_open(
    provisioned: Provisioned, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", provisioned.app_dsn)
    monkeypatch.setenv("DB_SCHEMA", provisioned.schema)
    app = create_app()
    metrics = InMemoryMetricReader()
    app.state.instruments = telemetry.instrument(
        app, telemetry_settings(), InMemorySpanExporter(), metrics
    )

    with TestClient(app) as client:
        client.get("/ready")
        collected = metrics.get_metrics_data()

    assert collected is not None
    points = [
        (metric.name, dict(point.attributes or {}), point.value)
        for resource in collected.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        for point in metric.data.data_points
        if metric.name.startswith("db.client.connection.") and isinstance(point, NumberDataPoint)
    ]
    pool = {"db.client.connection.pool.name": provisioned.schema}
    assert sorted(points, key=str) == sorted(
        [
            ("db.client.connection.count", {**pool, "db.client.connection.state": "used"}, 0),
            ("db.client.connection.count", {**pool, "db.client.connection.state": "idle"}, 1),
            ("db.client.connection.max", pool, 10),
        ],
        key=str,
    )
