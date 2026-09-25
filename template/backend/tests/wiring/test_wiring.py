"""What the environment turns telemetry into, and every configuration it refuses to start on."""

import pytest

from app.wiring import (
    HEADERS_ENV,
    NOT_READ,
    OTLP_ENDPOINT_ENV,
    PROTOCOL_ENV,
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
def configured(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """An endpoint and a service name: the least that is telemetry."""
    monkeypatch.setenv(OTLP_ENDPOINT_ENV, ENDPOINT)
    monkeypatch.setenv(SERVICE_NAME_ENV, "tasks")
    return monkeypatch


def test_no_endpoint_is_no_telemetry() -> None:
    assert build_telemetry() is None


@pytest.mark.parametrize("variable", NOT_READ)
def test_a_variable_this_process_does_not_read_is_harmless_while_telemetry_is_off(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    monkeypatch.setenv(variable, "true")

    assert build_telemetry() is None


def test_an_endpoint_and_a_name_are_telemetry_that_samples_everything_and_trusts_nobody(
    configured: pytest.MonkeyPatch,
) -> None:
    configured.setenv(OTLP_ENDPOINT_ENV, f"{ENDPOINT}/")

    assert build_telemetry() == TelemetrySettings(
        endpoint=ENDPOINT, service="tasks", sampling_ratio=1.0, trust_inbound_context=False
    )


def test_a_sampling_ratio_and_trust_are_read_when_given(configured: pytest.MonkeyPatch) -> None:
    configured.setenv(SAMPLING_RATIO_ENV, "0.1")
    configured.setenv(TRUST_INBOUND_CONTEXT_ENV, "1")

    settings = build_telemetry()

    assert settings is not None
    assert settings.sampling_ratio == 0.1
    assert settings.trust_inbound_context is True


@pytest.mark.parametrize(
    "variable", [SERVICE_NAME_ENV, SAMPLING_RATIO_ENV, TRUST_INBOUND_CONTEXT_ENV, HEADERS_ENV]
)
def test_a_variable_that_needs_the_endpoint_refuses_to_start_without_it(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    monkeypatch.setenv(variable, "1")

    with pytest.raises(TelemetryMisconfigured, match=OTLP_ENDPOINT_ENV):
        build_telemetry()


@pytest.mark.parametrize("variable", NOT_READ)
def test_a_variable_this_process_does_not_read_refuses_to_start_beside_the_endpoint(
    configured: pytest.MonkeyPatch, variable: str
) -> None:
    configured.setenv(variable, "true")

    with pytest.raises(TelemetryMisconfigured, match=variable):
        build_telemetry()


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        pytest.param(OTLP_ENDPOINT_ENV, "localhost:4318", id="an endpoint with no scheme"),
        pytest.param(PROTOCOL_ENV, "grpc", id="grpc"),
        pytest.param(SAMPLING_RATIO_ENV, "half", id="a ratio that is not a number"),
        pytest.param(SAMPLING_RATIO_ENV, "1.5", id="a ratio above one"),
        pytest.param(SAMPLING_RATIO_ENV, "nan", id="a ratio that is not one"),
        pytest.param(TRUST_INBOUND_CONTEXT_ENV, "maybe", id="trust that is neither yes nor no"),
        pytest.param(SEMCONV_ENV, "http/dup", id="other semantic conventions"),
    ],
)
def test_a_value_that_is_not_one_refuses_to_start(
    configured: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    configured.setenv(variable, value)

    with pytest.raises(TelemetryMisconfigured, match=variable):
        build_telemetry()


def test_an_endpoint_without_a_name_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OTLP_ENDPOINT_ENV, ENDPOINT)

    with pytest.raises(TelemetryMisconfigured, match=SERVICE_NAME_ENV):
        build_telemetry()
