"""What the environment turns telemetry, the bounds on Postgres and the connection draws into, and
every configuration it refuses to start on."""

import pytest

from app import connection_budget
from app.deployment import (
    ACQUIRE_TIMEOUT_ENV,
    DATABASE_URL_ENV,
    ENVIRONMENT_ENV,
    HEADERS_ENV,
    IDLE_IN_TRANSACTION_TIMEOUT_ENV,
    INSTANCES_ENV,
    NOT_READ,
    OTLP_ENDPOINT_ENV,
    PRODUCTION,
    PROTOCOL_ENV,
    RESOURCE_ATTRIBUTES_ENV,
    SAMPLING_RATIO_ENV,
    SEMCONV_ENV,
    SERVICE_NAME_ENV,
    STATEMENT_TIMEOUT_ENV,
    TRUST_INBOUND_CONTEXT_ENV,
)
from app.migrate import OWNER_URL_ENV
from app.store.budget import Draws
from app.store.pg import TIMEOUTS, Timeouts
from app.wiring import (
    InstancesMisconfigured,
    OwnerCredentialVisible,
    TelemetryMisconfigured,
    TelemetrySettings,
    TimeoutsMisconfigured,
    build,
    build_draws,
    build_service_version,
    build_telemetry,
    build_timeouts,
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


def test_no_resource_attributes_is_no_version() -> None:
    assert build_service_version() is None


@pytest.mark.parametrize(
    ("said", "version"),
    [
        ("service.version=1.4.2", "1.4.2"),
        (" deployment.environment.name = prod , service.version = 1.4.2 ", "1.4.2"),
        ("service.version=1.4.2%2Bbuild.7", "1.4.2+build.7"),
        ("deployment.environment.name=prod", None),
    ],
)
def test_the_version_is_read_from_the_resource_attributes_with_or_without_telemetry(
    monkeypatch: pytest.MonkeyPatch, said: str, version: str | None
) -> None:
    monkeypatch.setenv(RESOURCE_ATTRIBUTES_ENV, said)

    assert build_service_version() == version


@pytest.mark.parametrize("said", ["service.version", "=1.4.2", "service.version=1.4.2,"])
def test_resource_attributes_that_are_not_key_value_pairs_refuse_to_start(
    monkeypatch: pytest.MonkeyPatch, said: str
) -> None:
    monkeypatch.setenv(RESOURCE_ATTRIBUTES_ENV, said)

    with pytest.raises(TelemetryMisconfigured, match=RESOURCE_ATTRIBUTES_ENV):
        build_service_version()


def test_no_timeout_variable_is_the_shipped_bounds() -> None:
    assert build_timeouts() == TIMEOUTS


def test_each_timeout_a_deployment_sets_is_read_in_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(STATEMENT_TIMEOUT_ENV, "8")
    monkeypatch.setenv(IDLE_IN_TRANSACTION_TIMEOUT_ENV, " 600.5 ")
    monkeypatch.setenv(ACQUIRE_TIMEOUT_ENV, "0.25")

    assert build_timeouts() == Timeouts(statement=8.0, idle_in_transaction=600.5, acquire=0.25)


@pytest.mark.parametrize(
    ("variable", "said"),
    [(STATEMENT_TIMEOUT_ENV, "9"), (ACQUIRE_TIMEOUT_ENV, "10")],
    ids=["an-answer", "a-connection"],
)
def test_a_timeout_that_makes_a_request_wait_the_request_budget_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch, variable: str, said: str
) -> None:
    monkeypatch.setenv(variable, said)

    with pytest.raises(TimeoutsMisconfigured, match=variable):
        build_timeouts()


@pytest.mark.parametrize("said", ["soon", "0", "0.0005", "-1", "nan", "inf", "5s"])
def test_a_timeout_that_is_not_a_bound_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch, said: str
) -> None:
    monkeypatch.setenv(STATEMENT_TIMEOUT_ENV, said)

    with pytest.raises(TimeoutsMisconfigured, match=STATEMENT_TIMEOUT_ENV):
        build_timeouts()


def test_a_timeout_without_a_database_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DATABASE_URL_ENV, raising=False)
    monkeypatch.setenv(ACQUIRE_TIMEOUT_ENV, "1")

    with pytest.raises(TimeoutsMisconfigured, match=ACQUIRE_TIMEOUT_ENV):
        build()


@pytest.mark.parametrize(
    "database",
    [{}, {DATABASE_URL_ENV: "postgres://app_app@db.internal:5432/app"}],
    ids=["in-memory", "on-postgres"],
)
def test_a_process_that_can_see_the_owner_credential_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch, database: dict[str, str]
) -> None:
    for name, value in database.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(OWNER_URL_ENV, "postgres://app_owner@db.internal:5432/app")

    with pytest.raises(OwnerCredentialVisible, match=OWNER_URL_ENV):
        build()


def test_the_development_loop_is_one_process_holding_what_the_project_declares() -> None:
    assert build_draws() == Draws(
        instances=1,
        in_process=connection_budget.IN_PROCESS,
        outside=connection_budget.OUTSIDE,
    )


def test_the_instances_a_deployment_sets_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(INSTANCES_ENV, " 12 ")

    assert build_draws().instances == 12


def test_production_without_the_instances_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENVIRONMENT_ENV, PRODUCTION)

    with pytest.raises(InstancesMisconfigured, match=f"{INSTANCES_ENV} is unset"):
        build_draws()


@pytest.mark.parametrize("said", ["0", "-2", "1.5", "six", "2x"])
def test_instances_that_are_not_a_number_of_processes_refuse_to_start(
    monkeypatch: pytest.MonkeyPatch, said: str
) -> None:
    monkeypatch.setenv(INSTANCES_ENV, said)

    with pytest.raises(InstancesMisconfigured, match=INSTANCES_ENV):
        build_draws()


def test_instances_without_a_database_refuse_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(INSTANCES_ENV, "2")

    with pytest.raises(InstancesMisconfigured, match=DATABASE_URL_ENV):
        build()
