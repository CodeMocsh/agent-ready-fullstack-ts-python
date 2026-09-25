"""What a real Collector and the stores behind it kept, read back from them.

Every other telemetry test stops at the exporter. This one sends through OTLP to otel-lgtm --
the Collector, Tempo and Prometheus that `make observe` runs -- and asserts on what Tempo and
Prometheus return, so a field the wire format or the Collector adds is caught as well.
"""

import base64
import json
import time
from collections.abc import Callable, Iterator
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.telemetry import METRIC_ATTRIBUTES, SPAN_ATTRIBUTES
from app.wiring import OTLP_ENDPOINT_ENV, SERVICE_NAME_ENV

CANARY = "canary-6f1e2d-alice@example.com"
STATE_CANARY = "canary6f1e2d"
CALLER_TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
TRACEPARENT = f"00-{CALLER_TRACE}-00f067aa0ba902b7-01"
PATIENCE_SECONDS = 120
PROMETHEUS_OWN_LABELS = frozenset(
    {"__name__", "job", "instance", "service_name", "service_instance_id", "otel_scope_name"}
)


def eventually[T](read: Callable[[], T | None], what: str) -> T:
    """Polls `read` until it answers, because both stores index what they receive a little
    later and refuse queries while they start. Raises naming `what` once `PATIENCE_SECONDS`
    pass."""
    deadline = time.monotonic() + PATIENCE_SECONDS
    while time.monotonic() < deadline:
        answer = read()
        if answer is not None:
            return answer
        time.sleep(2)
    raise AssertionError(f"{what} never arrived within {PATIENCE_SECONDS}s")


def hexed(encoded: str) -> str:
    return base64.b64decode(encoded).hex()


def traced(grafana: str, service: str) -> list[dict[str, Any]] | None:
    now = int(time.time())
    answered = httpx.get(
        f"{grafana}/api/datasources/proxy/uid/tempo/api/search",
        params={"q": f'{{resource.service.name="{service}"}}', "start": now - 900, "end": now},
    )
    if answered.status_code != 200:
        return None
    found = answered.json()["traces"]
    return found if len(found) == 2 else None


def measured(grafana: str, service: str) -> list[dict[str, Any]] | None:
    answered = httpx.get(
        f"{grafana}/api/datasources/proxy/uid/prometheus/api/v1/query",
        params={"query": f'http_server_request_duration_seconds_count{{service_name="{service}"}}'},
    )
    if answered.status_code != 200:
        return None
    found = answered.json()["data"]["result"]
    return found if len(found) == 2 else None


@pytest.fixture(scope="module")
def service(otlp: str) -> Iterator[str]:
    """A service name no earlier run used, served once by an app exporting to the viewer."""
    named = f"observe-{uuid4().hex[:8]}"
    with pytest.MonkeyPatch.context() as env:
        env.delenv("DATABASE_URL", raising=False)
        env.setenv(OTLP_ENDPOINT_ENV, otlp)
        env.setenv(SERVICE_NAME_ENV, named)
        served()
        yield named


def served() -> None:
    with TestClient(create_app()) as client:
        client.get(
            f"/tasks?email={CANARY}",
            headers={"traceparent": TRACEPARENT, "tracestate": f"vendor={STATE_CANARY}"},
        )
        client.patch(f"/tasks/{CANARY}", json={"done": True}, headers={"x-note": CANARY})
        client.get("/health")
        client.get("/ready")


def test_tempo_keeps_only_declared_attributes_and_nothing_a_request_carried(
    grafana: str, service: str
) -> None:
    found = eventually(lambda: traced(grafana, service), "two traces")

    assert {one["rootTraceName"] for one in found} == {"GET /tasks", "PATCH /tasks/{id}"}
    for one in found:
        stored = httpx.get(
            f"{grafana}/api/datasources/proxy/uid/tempo/api/v2/traces/{one['traceID']}"
        )
        raw = stored.text
        assert CANARY not in raw
        assert STATE_CANARY not in raw
        for resource in stored.json()["trace"]["resourceSpans"]:
            for scope in resource["scopeSpans"]:
                for span in scope["spans"]:
                    assert {a["key"] for a in span.get("attributes", [])} <= SPAN_ATTRIBUTES
                    assert span.get("events", []) == []


def test_tempo_keeps_an_outside_caller_as_a_link_on_a_trace_of_its_own(
    grafana: str, service: str
) -> None:
    found = eventually(lambda: traced(grafana, service), "two traces")
    [listed] = [one for one in found if one["rootTraceName"] == "GET /tasks"]

    stored = httpx.get(
        f"{grafana}/api/datasources/proxy/uid/tempo/api/v2/traces/{listed['traceID']}"
    ).json()
    [span] = [
        span
        for resource in stored["trace"]["resourceSpans"]
        for scope in resource["scopeSpans"]
        for span in scope["spans"]
    ]

    assert hexed(span["traceId"]) != CALLER_TRACE
    [link] = span["links"]
    assert hexed(link["traceId"]) == CALLER_TRACE
    assert link.get("traceState", "") == ""


def test_prometheus_keeps_the_duration_by_route_template_and_never_a_probe(
    grafana: str, service: str
) -> None:
    found = eventually(lambda: measured(grafana, service), "two duration series")

    assert {one["metric"]["http_route"] for one in found} == {"/tasks", "/tasks/{id}"}
    declared = {name.replace(".", "_") for name in METRIC_ATTRIBUTES}
    for one in found:
        assert set(one["metric"]) - PROMETHEUS_OWN_LABELS <= declared
    assert CANARY not in json.dumps(found)
