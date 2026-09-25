"""What the environment turns telemetry into, and every configuration it refuses to start on."""

import pytest

from app.wiring import (
    OTLP_ENDPOINT_ENV,
    SAMPLING_RATIO_ENV,
    SEMCONV_ENV,
    SERVICE_NAME_ENV,
    TRUST_INBOUND_CONTEXT_ENV,
    TelemetryMisconfigured,
    TelemetrySettings,
    build_telemetry,
)

ENDPOINT = "http://collector:4318"


@pytest.fixture
def named(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """An endpoint and a service name: the least that is telemetry."""
    monkeypatch.setenv(OTLP_ENDPOINT_ENV, ENDPOINT)
    monkeypatch.setenv(SERVICE_NAME_ENV, "tasks")
    monkeypatch.delenv(SEMCONV_ENV, raising=False)
    return monkeypatch


def test_no_endpoint_is_no_telemetry() -> None:
    assert build_telemetry() is None


def test_an_endpoint_and_a_name_are_telemetry_that_samples_everything_and_trusts_nobody(
    named: pytest.MonkeyPatch,
) -> None:
    named.setenv(OTLP_ENDPOINT_ENV, f"{ENDPOINT}/")

    assert build_telemetry() == TelemetrySettings(
        endpoint=ENDPOINT, service="tasks", sampling_ratio=1.0, trust_inbound_context=False
    )


def test_a_sampling_ratio_and_trust_are_read_when_given(named: pytest.MonkeyPatch) -> None:
    named.setenv(SAMPLING_RATIO_ENV, "0.1")
    named.setenv(TRUST_INBOUND_CONTEXT_ENV, "1")

    settings = build_telemetry()

    assert settings is not None
    assert settings.sampling_ratio == 0.1
    assert settings.trust_inbound_context is True


@pytest.mark.parametrize(
    ("said", "refused"),
    [
        pytest.param({SERVICE_NAME_ENV: "tasks"}, OTLP_ENDPOINT_ENV, id="a name, no endpoint"),
        pytest.param({SAMPLING_RATIO_ENV: "0.5"}, OTLP_ENDPOINT_ENV, id="a ratio, no endpoint"),
        pytest.param({TRUST_INBOUND_CONTEXT_ENV: "1"}, OTLP_ENDPOINT_ENV, id="trust, no endpoint"),
        pytest.param(
            {"OTEL_EXPORTER_OTLP_HEADERS": "a=b"}, OTLP_ENDPOINT_ENV, id="headers, no endpoint"
        ),
        pytest.param(
            {"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": ENDPOINT},
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
            id="a per-signal endpoint",
        ),
        pytest.param({"OTEL_TRACES_SAMPLER": "always_on"}, "OTEL_TRACES_SAMPLER", id="a sampler"),
        pytest.param({"OTEL_SDK_DISABLED": "true"}, "OTEL_SDK_DISABLED", id="the SDK disabled"),
        pytest.param(
            {"OTEL_EXPORTER_OTLP_PROTOCOL": "grpc"}, "OTEL_EXPORTER_OTLP_PROTOCOL", id="grpc"
        ),
    ],
)
def test_what_this_process_would_not_act_on_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch, said: dict[str, str], refused: str
) -> None:
    for name, value in said.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(TelemetryMisconfigured, match=refused):
        build_telemetry()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        pytest.param(OTLP_ENDPOINT_ENV, "localhost:4318", id="an endpoint with no scheme"),
        pytest.param(SAMPLING_RATIO_ENV, "half", id="a ratio that is not a number"),
        pytest.param(SAMPLING_RATIO_ENV, "1.5", id="a ratio above one"),
        pytest.param(SAMPLING_RATIO_ENV, "nan", id="a ratio that is not one"),
        pytest.param(TRUST_INBOUND_CONTEXT_ENV, "maybe", id="trust that is neither yes nor no"),
        pytest.param(SEMCONV_ENV, "http/dup", id="other semantic conventions"),
    ],
)
def test_a_value_that_is_not_one_refuses_to_start(
    named: pytest.MonkeyPatch, name: str, value: str
) -> None:
    named.setenv(name, value)

    with pytest.raises(TelemetryMisconfigured, match=name):
        build_telemetry()
