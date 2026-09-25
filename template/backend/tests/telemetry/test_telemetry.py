"""Traces and metrics: what leaves the process, and what never does.

Driven through a real `TestClient` against an app instrumented with in-memory exporters, so
what is asserted is what an OTLP exporter would have been handed.
"""

import json
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, override

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, Status, StatusCode

from app import telemetry
from app.main import create_app
from app.wiring import OTLP_ENDPOINT_ENV, SERVICE_NAME_ENV, TelemetrySettings
from tests.conftest import Logged

CANARY = "canary-6f1e2d-alice@example.com"
CALLER_TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
CALLER_SPAN = "00f067aa0ba902b7"
TRACEPARENT = f"00-{CALLER_TRACE}-{CALLER_SPAN}-01"
TRACESTATE = "vendor=canary6f1e2d"


@dataclass
class Instrumented:
    client: TestClient
    app: FastAPI
    spans: InMemorySpanExporter
    metrics: InMemoryMetricReader
    instruments: telemetry.Instruments

    def finished(self) -> tuple[ReadableSpan, ...]:
        self.instruments.tracer_provider.force_flush()
        return self.spans.get_finished_spans()

    def servers(self) -> list[ReadableSpan]:
        return [one for one in self.finished() if one.kind is SpanKind.SERVER]

    def data_points(self) -> list[tuple[str, dict[str, Any]]]:
        collected = self.metrics.get_metrics_data()
        assert collected is not None
        return [
            (metric.name, dict(point.attributes or {}))
            for resource in collected.resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
            for point in metric.data.data_points
        ]


def instrumented(
    monkeypatch: pytest.MonkeyPatch, *, trust: bool, ratio: float = 1.0
) -> Iterator[Instrumented]:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = create_app()

    @app.get("/raises")
    async def raises() -> None:
        raise RuntimeError(CANARY)

    @app.get("/describes")
    async def describes() -> None:
        trace.get_current_span().set_status(Status(StatusCode.ERROR, CANARY))

    spans = InMemorySpanExporter()
    metrics = InMemoryMetricReader()
    settings = TelemetrySettings(
        endpoint="unused", service="tasks-test", sampling_ratio=ratio, trust_inbound_context=trust
    )
    instruments = telemetry.instrument(app, settings, spans, metrics)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield Instrumented(client, app, spans, metrics, instruments)
    instruments.shutdown()


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> Iterator[Instrumented]:
    yield from instrumented(monkeypatch, trust=False)


@pytest.fixture
def trusting(monkeypatch: pytest.MonkeyPatch) -> Iterator[Instrumented]:
    yield from instrumented(monkeypatch, trust=True)


@pytest.fixture
def unsampled(monkeypatch: pytest.MonkeyPatch) -> Iterator[Instrumented]:
    yield from instrumented(monkeypatch, trust=False, ratio=0.0)


def trace_of(span: ReadableSpan) -> str:
    assert span.context is not None
    return format(span.context.trace_id, "032x")


def everything_in(span: ReadableSpan) -> str:
    return json.dumps(
        {
            "name": span.name,
            "attributes": dict(span.attributes or {}),
            "events": [(one.name, dict(one.attributes or {})) for one in span.events],
            "status": span.status.description,
            "links": [dict(one.attributes or {}) for one in span.links],
            "states": [
                str(one.trace_state)
                for one in (span.context, span.parent, *(link.context for link in span.links))
                if one is not None
            ],
        }
    )


def test_a_request_is_one_server_span_named_by_its_route_with_only_declared_attributes(
    served: Instrumented,
) -> None:
    served.client.patch("/tasks/does-not-exist", json={"done": True})

    [span] = served.servers()

    assert span.name == "PATCH /tasks/{id}"
    assert span.attributes is not None
    assert span.attributes["http.route"] == "/tasks/{id}"
    assert span.attributes["http.response.status_code"] == 404
    assert set(span.attributes) <= telemetry.SPAN_ATTRIBUTES
    assert span.events == ()


def test_the_request_duration_is_recorded_by_route_template_and_nothing_finer(
    served: Instrumented,
) -> None:
    served.client.get("/tasks")

    durations = [
        attrs for name, attrs in served.data_points() if name == "http.server.request.duration"
    ]

    assert durations == [
        {"http.request.method": "GET", "http.route": "/tasks", "http.response.status_code": 200}
    ]
    for _, attributes in served.data_points():
        assert set(attributes) <= telemetry.METRIC_ATTRIBUTES


def test_nothing_the_request_carried_leaves_in_a_span_or_a_metric(served: Instrumented) -> None:
    """Body, query string, a header, baggage, a path parameter, a refused body, an exception's
    message, and a status description of the kind a library writes. The spans are counted too,
    because an app exporting nothing passes the absence check as happily as a clean one."""
    served.client.post(
        f"/tasks?note={CANARY}",
        json={"title": CANARY},
        headers={
            "x-note": CANARY,
            "baggage": f"user={CANARY}",
            "user-agent": CANARY,
            "traceparent": TRACEPARENT,
            "tracestate": TRACESTATE,
        },
    )
    served.client.patch(f"/tasks/{CANARY}", json={"done": True})
    served.client.post("/tasks", json={"title": {"nested": CANARY}})
    served.client.get(f"/no-route/{CANARY}")
    served.client.get("/raises")
    served.client.get("/describes")

    servers = served.servers()

    assert len(servers) == 6
    exported = "".join(everything_in(one) for one in served.finished())
    assert CANARY not in exported
    assert "canary6f1e2d" not in exported
    assert CANARY not in json.dumps(served.data_points())


def test_an_exception_is_named_by_its_status_and_never_by_its_message(served: Instrumented) -> None:
    served.client.get("/raises")

    [span] = served.servers()

    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description is None
    assert span.events == ()
    assert span.attributes is not None
    assert span.attributes["error.type"] == "500"


def test_a_caller_from_outside_starts_a_new_trace_and_is_kept_as_a_link(
    served: Instrumented,
) -> None:
    served.client.get("/tasks", headers={"traceparent": TRACEPARENT})

    [span] = served.servers()

    assert trace_of(span) != CALLER_TRACE
    assert span.parent is None
    [link] = span.links
    assert format(link.context.trace_id, "032x") == CALLER_TRACE
    assert format(link.context.span_id, "016x") == CALLER_SPAN


def test_a_trusted_caller_is_continued_in_the_same_trace(trusting: Instrumented) -> None:
    trusting.client.get("/tasks", headers={"traceparent": TRACEPARENT, "tracestate": TRACESTATE})

    [span] = trusting.servers()

    assert trace_of(span) == CALLER_TRACE
    assert "canary6f1e2d" not in everything_in(span)
    assert span.parent is not None
    assert format(span.parent.span_id, "016x") == CALLER_SPAN
    assert span.links == ()


def test_every_log_line_written_in_a_request_names_its_trace_and_span(
    served: Instrumented, logged: Logged
) -> None:
    logged()
    served.client.get("/tasks")

    [span] = served.servers()
    [line] = [one for one in logged() if one["message"] == "request completed"]

    assert line["trace_id"] == trace_of(span)
    assert len(line["span_id"]) == 16


def test_an_attribute_left_out_is_reported_once_by_name_and_never_by_value(
    served: Instrumented, logged: Logged
) -> None:
    served.client.get(f"/tasks?note={CANARY}")
    served.client.get(f"/tasks?note={CANARY}")
    served.finished()

    dropped = [one["attribute"] for one in logged() if one["message"] == "span attribute dropped"]

    assert "url.query" in dropped
    assert len(dropped) == len(set(dropped))
    assert CANARY not in json.dumps(dropped)


def test_a_request_sampled_out_exports_no_span_and_logs_no_trace_but_is_still_measured(
    unsampled: Instrumented, logged: Logged
) -> None:
    """A `trace_id` on a log line promises a trace somebody can open. Metrics are never
    sampled, so the error rate and latency stay exact whatever the ratio."""
    logged()
    unsampled.client.get("/tasks")

    lines = logged()
    durations = [n for n, _ in unsampled.data_points() if n == "http.server.request.duration"]

    assert unsampled.finished() == ()
    assert [one for one in lines if "trace_id" in one] == []
    assert durations == ["http.server.request.duration"]


def test_a_probe_is_neither_traced_nor_measured(served: Instrumented) -> None:
    served.client.get("/health")
    served.client.get("/ready")
    served.client.get("/tasks")

    routes = {attrs["http.route"] for _, attrs in served.data_points() if "http.route" in attrs}

    assert [one.name for one in served.servers()] == ["GET /tasks"]
    assert routes == {"/tasks"}


@pytest.mark.usefixtures("served")
def test_a_second_app_in_the_same_process_refuses_to_be_instrumented() -> None:
    settings = TelemetrySettings(
        endpoint="unused", service="tasks-test", sampling_ratio=1.0, trust_inbound_context=False
    )

    with pytest.raises(RuntimeError, match="already instrumented"):
        telemetry.instrument(create_app(), settings, InMemorySpanExporter(), InMemoryMetricReader())


def test_an_app_with_no_endpoint_is_not_instrumented(
    monkeypatch: pytest.MonkeyPatch, logged: Logged
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = create_app()

    with TestClient(app) as client:
        client.get("/tasks")

    assert app.state.instruments is None
    assert all("trace_id" not in line for line in logged())


def test_a_collector_that_does_not_answer_slows_no_request_and_delays_shutdown_by_its_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed = probe.getsockname()[1]
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv(OTLP_ENDPOINT_ENV, f"http://127.0.0.1:{closed}")
    monkeypatch.setenv(SERVICE_NAME_ENV, "tasks-test")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TIMEOUT", "1")

    with TestClient(create_app()) as client:
        started = time.monotonic()
        answered = client.get("/tasks")
        answering = time.monotonic() - started
        stopping = time.monotonic()
    stopped = time.monotonic() - stopping

    assert answered.status_code == 200
    assert answering < 0.5
    assert stopped < 5


class _Collector(BaseHTTPRequestHandler):
    received: list[str] = []

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers["content-length"]))
        _Collector.received.append(self.path)
        self.send_response(200)
        self.end_headers()

    @override
    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def collector() -> Iterator[str]:
    """Something listening where a Collector would, recording what was posted to it."""
    _Collector.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Collector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_an_app_given_an_endpoint_exports_traces_and_metrics_to_it_over_otlp(
    monkeypatch: pytest.MonkeyPatch, collector: str
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv(OTLP_ENDPOINT_ENV, collector)
    monkeypatch.setenv(SERVICE_NAME_ENV, "tasks-test")
    app = create_app()

    with TestClient(app) as client:
        client.get("/tasks")

    assert "/v1/traces" in _Collector.received
    assert "/v1/metrics" in _Collector.received
