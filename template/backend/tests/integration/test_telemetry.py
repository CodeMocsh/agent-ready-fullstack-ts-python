"""What a real Postgres produces: database spans named by operation, with no query text and no
value that was sent, only inside a request, and the pool's connections as metrics."""

import asyncio
import json
import signal
from collections.abc import Callable

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics.export import (
    HistogramDataPoint,
    InMemoryMetricReader,
    MetricsData,
    NumberDataPoint,
)
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from app import telemetry, worker
from app.deps import database_of
from app.loop_watch import TICK_SECONDS
from app.main import create_app
from app.store.pg import PostgresDatabase
from tests.clock import elapse
from tests.doubles import CANARY, telemetry_settings
from tests.integration.conftest import Provisioned, a_postgres_database
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


@pytest.fixture
def on_postgres(provisioned: Provisioned, monkeypatch: pytest.MonkeyPatch) -> None:
    """An app created in the test serves from the provisioned schema."""
    monkeypatch.setenv("DATABASE_URL", provisioned.app_dsn)
    monkeypatch.setenv("DB_SCHEMA", provisioned.schema)


def exported_by(app: FastAPI, drive: Callable[[TestClient], None]) -> tuple[ReadableSpan, ...]:
    """Returns the spans `app` exports while `drive` uses it. The instruments stop before it
    returns."""
    spans = InMemorySpanExporter()
    instruments = telemetry.instrument(app, telemetry_settings(), spans, InMemoryMetricReader())
    try:
        with TestClient(app) as client:
            drive(client)
        instruments.tracer_provider.force_flush()
    finally:
        instruments.shutdown()
    return spans.get_finished_spans()


@pytest.mark.usefixtures("on_postgres")
def test_a_query_is_a_span_named_by_its_operation_and_carrying_no_value() -> None:
    def sends(client: TestClient) -> None:
        assert client.post("/sent", json={"name": CANARY}).status_code == 200

    exported = exported_by(sending_a_value_to_postgres(create_app()), sends)

    queries = [one for one in exported if one.kind is SpanKind.CLIENT]
    assert queries
    for query in queries:
        assert query.attributes is not None
        assert query.attributes["db.system.name"] == "postgresql"
        assert "db.query.text" not in query.attributes
        assert set(query.attributes) <= telemetry.SPAN_ATTRIBUTES
    assert CANARY not in json.dumps([(one.name, dict(one.attributes or {})) for one in exported])


@pytest.mark.usefixtures("on_postgres")
def test_a_query_exports_only_inside_a_traced_request() -> None:
    app = sending_a_value_to_postgres(create_app())

    def queries_outside_and_inside_a_request(client: TestClient) -> None:
        assert client.portal is not None
        client.portal.call(outside_any_request, app)
        assert client.get("/ready").status_code == 200
        assert client.post("/sent", json={"name": "sent"}).status_code == 200

    exported = exported_by(app, queries_outside_and_inside_a_request)

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


@pytest.mark.usefixtures("on_postgres")
def test_the_pool_reports_its_connections_by_state_the_most_it_will_open_and_each_wait(
    provisioned: Provisioned,
) -> None:
    app = create_app()
    metrics = InMemoryMetricReader()
    app.state.instruments = telemetry.instrument(
        app, telemetry_settings(), InMemorySpanExporter(), metrics
    )

    with TestClient(app) as client:
        assert client.get("/ready").status_code == 200
        database = app.state.database
        assert isinstance(database, PostgresDatabase)
        assert client.portal is not None
        opened = client.portal.call(database.pool)
        with client.portal.wrap_async_context_manager(database.connection()):
            collected = metrics.get_metrics_data()

    [waited] = waits(collected, pool=provisioned.schema)
    assert waited.count >= 1
    assert tuple(waited.explicit_bounds) == telemetry.CONNECTION_WAIT_BUCKETS
    reported = by_state(collected, pool=provisioned.schema)
    assert reported.keys() == {
        ("db.client.connection.count", "used"),
        ("db.client.connection.count", "idle"),
        ("db.client.connection.max", None),
    }
    used = reported["db.client.connection.count", "used"]
    idle = reported["db.client.connection.count", "idle"]
    most = reported["db.client.connection.max", None]
    assert used >= 1
    assert idle >= 0
    assert used + idle <= most
    assert most == opened.get_max_size()


def waits(collected: MetricsData | None, *, pool: str) -> list[HistogramDataPoint]:
    """Each point of the connection wait histogram. Fails on a point that is not a histogram,
    names another pool or carries another attribute."""
    assert collected is not None
    points = [
        point
        for resource in collected.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == telemetry.CONNECTION_WAIT
        for point in metric.data.data_points
    ]
    waited: list[HistogramDataPoint] = []
    for point in points:
        assert isinstance(point, HistogramDataPoint)
        assert dict(point.attributes or {}) == {"db.client.connection.pool.name": pool}
        waited.append(point)
    return waited


def by_state(collected: MetricsData | None, *, pool: str) -> dict[tuple[str, str | None], float]:
    """Each connection count's value by its name and state. Fails on a point that is not a
    number, names another pool or carries another attribute, and on a point reported twice."""
    assert collected is not None
    points = [
        (metric.name, point)
        for resource in collected.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name.startswith("db.client.connection.")
        and metric.name != telemetry.CONNECTION_WAIT
        for point in metric.data.data_points
    ]
    reported: dict[tuple[str, str | None], float] = {}
    for name, point in points:
        assert isinstance(point, NumberDataPoint)
        attributes = dict(point.attributes or {})
        state = attributes.pop("db.client.connection.state", None)
        assert attributes == {"db.client.connection.pool.name": pool}
        assert isinstance(state, str | None)
        assert (name, state) not in reported
        reported[name, state] = point.value
    return reported


async def test_a_worker_reports_its_loop_and_its_pool(provisioned: Provisioned) -> None:
    metrics = InMemoryMetricReader()
    meters = telemetry.meters(telemetry_settings(), metrics)
    database = await a_postgres_database(provisioned)

    async with asyncio.TaskGroup() as group:
        _ = group.create_task(worker.serve(database, meters))
        reported = await _until_reported(metrics, telemetry.LOOP_DELAY)
        signal.raise_signal(signal.SIGTERM)

    assert {
        telemetry.LOOP_DELAY,
        telemetry.CONNECTION_WAIT,
        "db.client.connection.count",
    } <= reported


async def _until_reported(metrics: InMemoryMetricReader, name: str) -> set[str]:
    """The names of the metrics `metrics` holds, once `name` is one of them. The worker handles
    SIGTERM before its first tick records the delay. Raises `TimeoutError` after 10 s."""
    async with asyncio.timeout(10):
        while True:
            collected = metrics.get_metrics_data()
            names = (
                set()
                if collected is None
                else {
                    metric.name
                    for resource in collected.resource_metrics
                    for scope in resource.scope_metrics
                    for metric in scope.metrics
                }
            )
            if name in names:
                return names
            await elapse(TICK_SECONDS)
