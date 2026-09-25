"""Traces and metrics: what leaves the process, and what never does.

Driven through a real `TestClient` against an app instrumented with in-memory exporters, so
what is asserted is what an OTLP exporter would have been handed.
"""

import json
import re
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

from app import serve, telemetry
from app.main import create_app
from app.wiring import OTLP_ENDPOINT_ENV, SERVICE_NAME_ENV
from tests.conftest import Logged
from tests.doubles import CANARY, telemetry_settings

STATE_CANARY = "canary6f1e2d"
CALLER_TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
CALLER_SPAN = "00f067aa0ba902b7"
TRACEPARENT = f"00-{CALLER_TRACE}-{CALLER_SPAN}-01"
TRACESTATE = f"vendor={STATE_CANARY}"
EXPORT_TIMEOUT_SECONDS = 1


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

    def points(self) -> list[tuple[str, Any]]:
        collected = self.metrics.get_metrics_data()
        assert collected is not None
        return [
            (metric.name, point)
            for resource in collected.resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
            for point in metric.data.data_points
        ]

    def data_points(self) -> list[tuple[str, dict[str, Any]]]:
        return [(name, dict(point.attributes or {})) for name, point in self.points()]

    def durations(self) -> list[dict[str, Any]]:
        return [
            attrs for name, attrs in self.data_points() if name == "http.server.request.duration"
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
    settings = telemetry_settings(sampling_ratio=ratio, trust_inbound_context=trust)
    instruments = telemetry.instrument(app, settings, spans, metrics)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield Instrumented(client, app, spans, metrics, instruments)
    instruments.shutdown()


@pytest.fixture
def untrusting(monkeypatch: pytest.MonkeyPatch) -> Iterator[Instrumented]:
    yield from instrumented(monkeypatch, trust=False)


@pytest.fixture
def trusting(monkeypatch: pytest.MonkeyPatch) -> Iterator[Instrumented]:
    yield from instrumented(monkeypatch, trust=True)


@pytest.fixture
def unsampled(monkeypatch: pytest.MonkeyPatch) -> Iterator[Instrumented]:
    yield from instrumented(monkeypatch, trust=False, ratio=0.0)


@pytest.fixture
def trusting_unsampled(monkeypatch: pytest.MonkeyPatch) -> Iterator[Instrumented]:
    yield from instrumented(monkeypatch, trust=True, ratio=0.0)


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
    untrusting: Instrumented,
) -> None:
    untrusting.client.patch("/tasks/does-not-exist", json={"done": True})

    [span] = untrusting.servers()

    assert span.name == "PATCH /tasks/{id}"
    assert span.attributes is not None
    assert span.attributes["http.route"] == "/tasks/{id}"
    assert span.attributes["http.response.status_code"] == 404
    assert set(span.attributes) <= telemetry.SPAN_ATTRIBUTES
    assert span.events == ()


def test_the_request_duration_is_recorded_by_route_template_with_no_exemplar(
    untrusting: Instrumented,
) -> None:
    untrusting.client.get("/tasks")

    points = untrusting.points()
    durations = [dict(p.attributes or {}) for n, p in points if n == "http.server.request.duration"]

    assert durations == [
        {"http.request.method": "GET", "http.route": "/tasks", "http.response.status_code": 200}
    ]
    for _, point in points:
        assert set(point.attributes or {}) <= telemetry.METRIC_ATTRIBUTES
        assert list(getattr(point, "exemplars", [])) == []


def test_nothing_the_request_carried_leaves_in_a_span_or_a_metric(
    untrusting: Instrumented,
) -> None:
    untrusting.client.post(
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
    untrusting.client.patch(f"/tasks/{CANARY}", json={"done": True})
    untrusting.client.post("/tasks", json={"title": {"nested": CANARY}})
    untrusting.client.get(f"/no-route/{CANARY}")
    untrusting.client.get("/raises")
    untrusting.client.get("/describes")

    exported = "".join(everything_in(one) for one in untrusting.finished())

    assert len(untrusting.servers()) == 6
    assert len(untrusting.durations()) >= 4
    assert CANARY not in exported
    assert STATE_CANARY not in exported
    assert CANARY not in json.dumps(untrusting.data_points())


def test_an_exception_is_named_by_its_status_and_never_by_its_message(
    untrusting: Instrumented,
) -> None:
    untrusting.client.get("/raises")

    [span] = untrusting.servers()

    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description is None
    assert span.events == ()
    assert span.attributes is not None
    assert span.attributes["error.type"] == "500"


def test_a_caller_from_outside_starts_a_new_trace_and_is_kept_as_a_link(
    untrusting: Instrumented,
) -> None:
    untrusting.client.get("/tasks", headers={"traceparent": TRACEPARENT})

    [span] = untrusting.servers()

    assert trace_of(span) != CALLER_TRACE
    assert span.parent is None
    [link] = span.links
    assert format(link.context.trace_id, "032x") == CALLER_TRACE
    assert format(link.context.span_id, "016x") == CALLER_SPAN


def test_a_trusted_caller_is_continued_in_the_same_trace(trusting: Instrumented) -> None:
    trusting.client.get("/tasks", headers={"traceparent": TRACEPARENT, "tracestate": TRACESTATE})

    [span] = trusting.servers()

    assert trace_of(span) == CALLER_TRACE
    assert STATE_CANARY not in everything_in(span)
    assert span.parent is not None
    assert format(span.parent.span_id, "016x") == CALLER_SPAN
    assert span.links == ()


def test_a_trusted_caller_decides_whether_its_trace_is_sampled(
    trusting_unsampled: Instrumented,
) -> None:
    trusting_unsampled.client.get("/tasks", headers={"traceparent": TRACEPARENT})
    trusting_unsampled.client.get("/tasks")

    [span] = trusting_unsampled.servers()

    assert trace_of(span) == CALLER_TRACE


def test_every_log_line_written_in_a_sampled_request_names_its_trace_and_span(
    untrusting: Instrumented, logged: Logged
) -> None:
    logged()
    untrusting.client.get("/tasks")

    [span] = untrusting.servers()
    [line] = [one for one in logged() if one["message"] == "request completed"]

    assert line["trace_id"] == trace_of(span)
    assert len(line["span_id"]) == 16


def test_a_request_sampled_out_exports_no_span_and_logs_no_trace_but_is_still_measured(
    unsampled: Instrumented, logged: Logged
) -> None:
    logged()
    unsampled.client.get("/tasks")

    lines = logged()

    assert unsampled.finished() == ()
    assert [one for one in lines if "trace_id" in one] == []
    assert len(unsampled.durations()) == 1


def test_an_attribute_left_out_is_reported_once_by_name_and_never_by_value(
    untrusting: Instrumented, logged: Logged
) -> None:
    untrusting.client.get(f"/tasks?note={CANARY}")
    untrusting.client.get(f"/tasks?note={CANARY}")
    untrusting.finished()

    dropped = [one["attribute"] for one in logged() if one["message"] == "span attribute dropped"]

    assert "url.query" in dropped
    assert len(dropped) == len(set(dropped))
    assert CANARY not in json.dumps(dropped)


def test_a_probe_is_neither_traced_nor_measured_and_a_route_named_like_one_is(
    untrusting: Instrumented,
) -> None:
    untrusting.client.get("/health")
    untrusting.client.get("/ready")
    untrusting.client.patch("/tasks/health", json={"done": True})
    untrusting.client.get("/tasks")

    routes = {attrs["http.route"] for attrs in untrusting.durations()}

    assert sorted(one.name for one in untrusting.servers()) == ["GET /tasks", "PATCH /tasks/{id}"]
    assert routes == {"/tasks", "/tasks/{id}"}


@pytest.mark.parametrize("path", ["/health", "/ready"])
def test_a_probe_under_the_one_origin_prefix_is_a_probe_too(path: str) -> None:
    assert re.search(telemetry.PROBES, f"http://testserver{serve.PREFIX}{path}")
    assert not re.search(telemetry.PROBES, f"http://testserver{serve.PREFIX}/tasks{path}")


@pytest.mark.usefixtures("untrusting")
def test_a_second_app_in_the_same_process_refuses_to_be_instrumented() -> None:
    with pytest.raises(RuntimeError, match="already instrumented"):
        telemetry.instrument(
            create_app(), telemetry_settings(), InMemorySpanExporter(), InMemoryMetricReader()
        )


def test_an_app_with_no_endpoint_is_not_instrumented(
    monkeypatch: pytest.MonkeyPatch, logged: Logged
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = create_app()

    with TestClient(app) as client:
        client.get("/tasks")

    assert app.state.instruments is None
    assert all("trace_id" not in line for line in logged())


@pytest.fixture
def silent_collector() -> Iterator[str]:
    """Accepts connections and never answers, like a Collector that has hung."""
    with socket.socket() as listening:
        listening.bind(("127.0.0.1", 0))
        listening.listen(64)
        yield f"http://127.0.0.1:{listening.getsockname()[1]}"


def test_a_collector_that_hangs_slows_no_request_and_holds_shutdown_to_two_timeouts(
    monkeypatch: pytest.MonkeyPatch, silent_collector: str
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv(OTLP_ENDPOINT_ENV, silent_collector)
    monkeypatch.setenv(SERVICE_NAME_ENV, "tasks-test")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TIMEOUT", str(EXPORT_TIMEOUT_SECONDS))

    with TestClient(create_app()) as client:
        started = time.monotonic()
        answered = client.get("/tasks")
        answering = time.monotonic() - started
        stopping = time.monotonic()
    stopped = time.monotonic() - stopping

    assert answered.status_code == 200
    assert answering < 0.5
    assert stopped < 2 * EXPORT_TIMEOUT_SECONDS + 1.5


@pytest.fixture
def collector() -> Iterator[tuple[str, list[str]]]:
    """Something listening where a Collector would, and the paths posted to it."""
    received: list[str] = []

    class Recording(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers["content-length"]))
            received.append(self.path)
            self.send_response(200)
            self.end_headers()

        @override
        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Recording)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", received
    server.shutdown()


def test_an_app_given_an_endpoint_exports_traces_and_metrics_to_it_over_otlp(
    monkeypatch: pytest.MonkeyPatch, collector: tuple[str, list[str]]
) -> None:
    endpoint, received = collector
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv(OTLP_ENDPOINT_ENV, endpoint)
    monkeypatch.setenv(SERVICE_NAME_ENV, "tasks-test")

    with TestClient(create_app()) as client:
        client.get("/tasks")

    assert "/v1/traces" in received
    assert "/v1/metrics" in received
