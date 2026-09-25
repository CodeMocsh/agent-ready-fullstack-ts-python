"""The database spans a real Postgres produces: named by operation, with no query text and no
value that was sent."""

import json

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from app import telemetry
from app.main import create_app
from app.wiring import TelemetrySettings
from tests.integration.conftest import Provisioned

CANARY = "canary-6f1e2d-alice@example.com"


def test_a_query_is_a_span_named_by_its_operation_and_carrying_no_value(
    provisioned: Provisioned, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", provisioned.app_dsn)
    monkeypatch.setenv("DB_SCHEMA", provisioned.schema)
    app = create_app()
    spans = InMemorySpanExporter()
    settings = TelemetrySettings(
        endpoint="unused", service="tasks-test", sampling_ratio=1.0, trust_inbound_context=False
    )
    instruments = telemetry.instrument(app, settings, spans, InMemoryMetricReader())

    try:
        with TestClient(app) as client:
            created = client.post("/tasks", json={"title": CANARY}).json()
            client.patch(f"/tasks/{created['id']}", json={"done": True})
            client.get("/tasks")
        instruments.tracer_provider.force_flush()
    finally:
        instruments.shutdown()

    queries = [one for one in spans.get_finished_spans() if one.kind is SpanKind.CLIENT]
    assert queries
    for query in queries:
        assert query.attributes is not None
        assert query.attributes["db.system.name"] == "postgresql"
        assert "db.query.text" not in query.attributes
        assert set(query.attributes) <= telemetry.SPAN_ATTRIBUTES
    assert CANARY not in json.dumps(
        [(one.name, dict(one.attributes or {})) for one in spans.get_finished_spans()]
    )
