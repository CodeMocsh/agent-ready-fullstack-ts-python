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
from tests.doubles import CANARY, telemetry_settings
from tests.integration.conftest import Provisioned


def test_a_query_is_a_span_named_by_its_operation_and_carrying_no_value(
    provisioned: Provisioned, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", provisioned.app_dsn)
    monkeypatch.setenv("DB_SCHEMA", provisioned.schema)
    app = create_app()
    spans = InMemorySpanExporter()
    instruments = telemetry.instrument(app, telemetry_settings(), spans, InMemoryMetricReader())

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
