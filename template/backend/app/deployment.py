"""Whether this process is the development loop or production, the names of the variables the
template's code reads, and how production refuses what only the development loop may carry.

The template owns this module and updates it. What production refuses is the project's, in
`app/environment.py`, which the template writes once. `docs/adr/template/0010` and
`docs/adr/template/0015`.

**An unset `APP_ENV` is production**, so a deployment that forgets the variable is checked
rather than waved through.
"""

import os
from typing import Final

DATABASE_URL_ENV: Final = "DATABASE_URL"
BUNDLE_ENV: Final = "FRONTEND_BUNDLE"
ACKNOWLEDGED_ENV: Final = "UNAUTHENTICATED_IS_INTENTIONAL"
"""Set by a deployment that means to serve everybody, so it is told at `INFO` rather than
warned on every boot. It changes a log level and nothing else -- `docs/adr/template/0004`."""

OTLP_ENDPOINT_ENV: Final = "OTEL_EXPORTER_OTLP_ENDPOINT"
SERVICE_NAME_ENV: Final = "OTEL_SERVICE_NAME"
SAMPLING_RATIO_ENV: Final = "OTEL_TRACES_SAMPLER_ARG"
TRUST_INBOUND_CONTEXT_ENV: Final = "TRUST_INBOUND_TRACE_CONTEXT"
SEMCONV_ENV: Final = "OTEL_SEMCONV_STABILITY_OPT_IN"
STABLE_SEMCONV: Final = "http,database"
"""The only value of `SEMCONV_ENV` this process accepts, and the one it sets itself."""
RESOURCE_ATTRIBUTES_ENV: Final = "OTEL_RESOURCE_ATTRIBUTES"
"""Read for `service.version`, which every log line carries whether or not telemetry is on. The
SDK reads the whole of it for the spans and metrics."""
PROTOCOL_ENV: Final = "OTEL_EXPORTER_OTLP_PROTOCOL"
HEADERS_ENV: Final = "OTEL_EXPORTER_OTLP_HEADERS"

NEEDS_THE_ENDPOINT: Final = (
    SERVICE_NAME_ENV,
    SAMPLING_RATIO_ENV,
    TRUST_INBOUND_CONTEXT_ENV,
    HEADERS_ENV,
)
"""Variables that mean something only once the endpoint is named."""

NOT_READ: Final = (
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
    "OTEL_TRACES_SAMPLER",
    "OTEL_TRACES_EXPORTER",
    "OTEL_METRICS_EXPORTER",
    "OTEL_PROPAGATORS",
    "OTEL_SDK_DISABLED",
    "OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_REQUEST",
    "OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_RESPONSE",
)
"""Variables the SDK would honour and this process does not. Refused alongside the endpoint."""

TELEMETRY_ENV: Final = (
    OTLP_ENDPOINT_ENV,
    PROTOCOL_ENV,
    RESOURCE_ATTRIBUTES_ENV,
    SEMCONV_ENV,
    *NEEDS_THE_ENDPOINT,
    *NOT_READ,
)
"""Every variable `wiring.build_telemetry` and `wiring.build_service_version` read."""

STATEMENT_TIMEOUT_ENV: Final = "DATABASE_STATEMENT_TIMEOUT"
IDLE_IN_TRANSACTION_TIMEOUT_ENV: Final = "DATABASE_IDLE_IN_TRANSACTION_TIMEOUT"
ACQUIRE_TIMEOUT_ENV: Final = "DATABASE_ACQUIRE_TIMEOUT"
TIMEOUTS_ENV: Final = (STATEMENT_TIMEOUT_ENV, IDLE_IN_TRANSACTION_TIMEOUT_ENV, ACQUIRE_TIMEOUT_ENV)
"""Every variable `wiring.build_timeouts` reads, each in seconds, and each meaningful only beside
`DATABASE_URL`."""

INSTANCES_ENV: Final = "DATABASE_INSTANCES"
"""The most processes that hold connections to the database at once, the processes of a revision
that drains during a rollout included. Meaningful only beside `DATABASE_URL`, and required there in
production. `docs/adr/template/0022`."""

ADMISSION_IN_FLIGHT_ENV: Final = "ADMISSION_IN_FLIGHT"
ADMISSION_PER_SECOND_ENV: Final = "ADMISSION_PER_SECOND"
ADMISSION_BURST_ENV: Final = "ADMISSION_BURST"
ADMISSION_STREAMS_ENV: Final = "ADMISSION_STREAMS"
ADMISSION_MODE_ENV: Final = "ADMISSION_MODE"
"""`enforce`, the default, refuses a request over a bound. `evaluate` reports it and admits it."""
ADMISSION_ENV: Final = (
    ADMISSION_IN_FLIGHT_ENV,
    ADMISSION_PER_SECOND_ENV,
    ADMISSION_BURST_ENV,
    ADMISSION_STREAMS_ENV,
    ADMISSION_MODE_ENV,
)
"""Every variable `wiring.build_admission` reads. Each number replaces the field of
`app/admission_limits.py` it names. `docs/adr/template/0023`."""

RESPONSE_MAX_BYTES_ENV: Final = "RESPONSE_MAX_BYTES"
RESPONSE_CEILING_BYTES_ENV: Final = "RESPONSE_CEILING_BYTES"
RESPONSE_ENV: Final = (RESPONSE_MAX_BYTES_ENV, RESPONSE_CEILING_BYTES_ENV)

READS_PART_BOUND_SECONDS_ENV: Final = "READS_PART_BOUND_SECONDS"
READS_MAX_PARTS_ENV: Final = "READS_MAX_PARTS"
READS_PART_CONCURRENCY_ENV: Final = "READS_PART_CONCURRENCY"
READS_ENV: Final = (READS_PART_BOUND_SECONDS_ENV, READS_MAX_PARTS_ENV, READS_PART_CONCURRENCY_ENV)
"""Every variable `wiring.build_reads` reads. Each one replaces the field of
`app/read_limits.py` it names. `docs/adr/template/0029`."""

ENVIRONMENT_ENV: Final = "APP_ENV"
DEVELOPMENT: Final = "development"
PRODUCTION: Final = "production"


def stated(name: str) -> str:
    """What the deployment set, stripped; empty when it set nothing."""
    return os.environ.get(name, "").strip()


class UnknownEnvironment(RuntimeError):
    """`APP_ENV` names neither `development` nor `production`."""


class DevelopmentSettingInProduction(RuntimeError):
    """A process that is not the development loop carries something only that loop may."""


def in_development() -> bool:
    """Whether `APP_ENV=development`. Unset or `production` is production; anything else
    raises `UnknownEnvironment`."""
    named = stated(ENVIRONMENT_ENV)
    if named in ("", PRODUCTION):
        return False
    if named == DEVELOPMENT:
        return True
    raise UnknownEnvironment(
        f"{ENVIRONMENT_ENV} is {named!r}. It takes {DEVELOPMENT!r} or {PRODUCTION!r}, and unset "
        f"means {PRODUCTION!r}."
    )


def refuse_in_production(found: list[str]) -> None:
    """Raise `DevelopmentSettingInProduction` naming every finding at once, unless this is the
    development loop. `found` is what `app/environment.py` says production may not carry."""
    if in_development() or not found:
        return
    raise DevelopmentSettingInProduction(
        f"{ENVIRONMENT_ENV} is not {DEVELOPMENT!r}, so this is production, and it carries "
        "what only the development loop may carry:\n- " + "\n- ".join(found)
    )
