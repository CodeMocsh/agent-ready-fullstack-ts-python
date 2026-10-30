"""What the lifespan does with the instruments: it records how late the event loop ran, it shuts
them down after the last request, and the shutdown flushes what the app recorded to the
Collector, within two export timeouts."""

import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import override

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import telemetry
from app.deployment import OTLP_ENDPOINT_ENV, SERVICE_NAME_ENV
from app.loop_watch import TICK_SECONDS
from app.main import create_app
from app.wiring import TelemetrySettings
from tests.widgets import with_widgets

EXPORT_TIMEOUT_SECONDS = 1


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
    monkeypatch.setenv(OTLP_ENDPOINT_ENV, silent_collector)
    monkeypatch.setenv(SERVICE_NAME_ENV, "tasks-test")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TIMEOUT", str(EXPORT_TIMEOUT_SECONDS))

    with TestClient(with_widgets(create_app())) as client:
        routed = client.get("/widgets")
        started = time.monotonic()
        answered = client.get("/widgets")
        answering = time.monotonic() - started
        stopping = time.monotonic()
    stopped = time.monotonic() - stopping

    assert routed.status_code == answered.status_code == 200
    assert answering < EXPORT_TIMEOUT_SECONDS / 2
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
    monkeypatch.setenv(OTLP_ENDPOINT_ENV, endpoint)
    monkeypatch.setenv(SERVICE_NAME_ENV, "tasks-test")

    with TestClient(with_widgets(create_app())) as client:
        client.get("/widgets")

    assert "/v1/traces" in received
    assert "/v1/metrics" in received


def test_a_started_app_records_how_late_its_event_loop_ran(monkeypatch: pytest.MonkeyPatch) -> None:
    metrics = InMemoryMetricReader()

    def in_memory(
        _settings: TelemetrySettings,
    ) -> tuple[InMemorySpanExporter, InMemoryMetricReader]:
        return InMemorySpanExporter(), metrics

    monkeypatch.setattr(telemetry, "otlp", in_memory)
    monkeypatch.setenv(OTLP_ENDPOINT_ENV, "http://unused")
    monkeypatch.setenv(SERVICE_NAME_ENV, "tasks-test")

    with TestClient(create_app()):
        time.sleep(3 * TICK_SECONDS)
        collected = metrics.get_metrics_data()

    assert collected is not None
    assert telemetry.LOOP_DELAY in {
        metric.name
        for resource in collected.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
