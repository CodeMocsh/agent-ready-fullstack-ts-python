"""Which environment this process is, the names of the variables the template's code reads, and
how production refuses what only development may carry.

The template owns this module and updates it. What production refuses is the project's, in
`app/environment.py`, which the template writes once. `docs/adr/template/0014` and
`docs/adr/template/0017`.

**An unset `APP_ENV` is production**, so a deployment that forgets the variable is checked
rather than waved through.
"""

import os
from typing import Final

DATABASE_URL_ENV: Final = "DATABASE_URL"
BUNDLE_ENV: Final = "FRONTEND_BUNDLE"
ACKNOWLEDGED_ENV: Final = "UNAUTHENTICATED_IS_INTENTIONAL"
"""Set by a deployment that means to serve everybody, so it is told at `INFO` rather than
warned on every boot. It changes a log level and nothing else -- `docs/adr/template/0008`."""

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
        "what only development may:\n- " + "\n- ".join(found)
    )
