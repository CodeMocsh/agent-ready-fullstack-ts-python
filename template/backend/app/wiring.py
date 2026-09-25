"""The one swap point: which substrate this process gets, and which frontend.

`build()` is where the environment is read, and it reads every variable this process uses
rather than letting them be picked up further down. That is what lets one process hold two
substrates at once — what the contract suite does — and what keeps a test from mutating
`os.environ` to choose one.

**`build_bundle()` is the same idea for the one-origin entrypoint**, and only `app.serve`
calls it. It lives here rather than there so that everything this deployment reads out of its
environment is in one file, which is what makes the list reviewable.

**No `DATABASE_URL` means the in-memory substrate**, so a fresh clone runs with no
infrastructure. That is a default, not a fallback: nothing here degrades from Postgres to
memory on an error, because a deployment that silently came up on memory has data that will
not be there tomorrow. A malformed `DATABASE_URL` fails at boot.

**And the application refuses to hold the owner credential.** Seeing `DATABASE_OWNER_URL` is
a refusal rather than a warning, for the reason `FORCE ROW LEVEL SECURITY` beats `ENABLE`: a
separation that depends on nobody making a mistake is not a separation. A single container
that migrates and then serves drops it between the two — `env -u DATABASE_OWNER_URL uvicorn`.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from app.identity import SENTINEL_TENANT
from app.migrate import OWNER_URL_ENV
from app.store import Database
from app.store.conn import SCHEMA_ENV
from app.store.memory import MemoryDatabase

DATABASE_URL_ENV: Final = "DATABASE_URL"
BUNDLE_ENV: Final = "FRONTEND_BUNDLE"
ACKNOWLEDGED_ENV: Final = "UNAUTHENTICATED_IS_INTENTIONAL"
"""Set by a deployment that means to serve everybody, so it is told at `INFO` rather than
warned on every boot. It changes a log level and nothing else -- `docs/adr/0008`.
"""


DENIALS: Final = frozenset({"", "0", "false", "no", "off"})
"""Spellings of "no" that must not read as an acknowledgement.

Any non-empty value counting would make `UNAUTHENTICATED_IS_INTENTIONAL=0` silence the
warning, which is the opposite of what somebody typing it meant -- and the only way they
would find out is by not being told.
"""


def unauthenticated_is_acknowledged() -> bool:
    """Whether this deployment has said out loud that it serves everybody on purpose."""
    return os.environ.get(ACKNOWLEDGED_ENV, "").strip().lower() not in DENIALS


OTLP_ENDPOINT_ENV: Final = "OTEL_EXPORTER_OTLP_ENDPOINT"
SERVICE_NAME_ENV: Final = "OTEL_SERVICE_NAME"
SAMPLING_RATIO_ENV: Final = "OTEL_TRACES_SAMPLER_ARG"
TRUST_INBOUND_CONTEXT_ENV: Final = "TRUST_INBOUND_TRACE_CONTEXT"
SEMCONV_ENV: Final = "OTEL_SEMCONV_STABILITY_OPT_IN"
STABLE_SEMCONV: Final = "http,database"
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
    SEMCONV_ENV,
    *NEEDS_THE_ENDPOINT,
    *NOT_READ,
)
"""Every variable `build_telemetry` reads."""

AFFIRMATIONS: Final = frozenset({"1", "true", "yes", "on"})

EVERY_TRACE: Final = 1.0
"""The sampling ratio when a deployment names none."""


@dataclass(frozen=True)
class TelemetrySettings:
    """Where traces and metrics go, what the service is called there, and whom it believes."""

    endpoint: str
    service: str
    sampling_ratio: float
    trust_inbound_context: bool


class TelemetryMisconfigured(RuntimeError):
    """The environment says something about telemetry this process will not act on, so it
    refuses to start."""


class OwnerCredentialVisible(RuntimeError):
    """The application can see `DATABASE_OWNER_URL`, and it must not be able to."""


class BundleMissing(RuntimeError):
    """`app.serve` was asked to put a frontend on the origin and there is not one to put."""


def build_bundle() -> Path:
    """Where the built frontend is, for the process that serves both halves.

    No default, because there is no honest one. `app.serve` exists to carry a bundle on the
    same origin as the API, so a deployment that started it without saying where the bundle is
    has not chosen a fallback — it has configured nothing, and every path that is not `/api`
    would answer with a file this process never found. `app.main` never reads this.
    """
    named = os.environ.get(BUNDLE_ENV, "").strip()
    if named == "":
        raise BundleMissing(
            f"{BUNDLE_ENV} is unset and `app.serve` has no frontend to put on the origin. "
            f"Point it at the directory `make build` wrote, or run `app.main` behind a proxy "
            f"that strips the prefix instead."
        )
    return Path(named)


def build_telemetry() -> TelemetrySettings | None:
    """Telemetry, when `OTEL_EXPORTER_OTLP_ENDPOINT` names a Collector over HTTP; otherwise
    `None`, and nothing is instrumented.

    Refuses to start on anything it would not act on: without the endpoint, a variable from
    `NEEDS_THE_ENDPOINT`; with it, one from `NOT_READ`, a protocol other than `http/protobuf`,
    an endpoint that is not a URL, an unparsable ratio or trust flag, or a semantic-convention
    choice other than `STABLE_SEMCONV`.
    """
    endpoint = _named(OTLP_ENDPOINT_ENV).rstrip("/")
    if endpoint == "":
        _refuse_any_of(
            NEEDS_THE_ENDPOINT, f"and {OTLP_ENDPOINT_ENV} unset: nothing would be exported"
        )
        return None
    _refuse_any_of(NOT_READ, "and this process does not read it")
    if _named(PROTOCOL_ENV) not in ("", "http/protobuf"):
        raise TelemetryMisconfigured(
            f"{PROTOCOL_ENV}={_named(PROTOCOL_ENV)!r}: this process exports over http/protobuf "
            f"only. Point it at the Collector's HTTP port."
        )
    if _named(SEMCONV_ENV) not in ("", STABLE_SEMCONV):
        raise TelemetryMisconfigured(
            f"{SEMCONV_ENV}={_named(SEMCONV_ENV)!r}: the declared attributes are the stable "
            f"conventions' names, so this process sets {STABLE_SEMCONV!r} itself. Unset it."
        )
    if not endpoint.startswith(("http://", "https://")):
        raise TelemetryMisconfigured(
            f"{OTLP_ENDPOINT_ENV}={endpoint!r} is not an http:// or https:// URL."
        )
    if _named(SERVICE_NAME_ENV) == "":
        raise TelemetryMisconfigured(
            f"{OTLP_ENDPOINT_ENV} is set and {SERVICE_NAME_ENV} is not. Every span and metric "
            f"is filed under the service name, so name this one."
        )
    return TelemetrySettings(
        endpoint=endpoint,
        service=_named(SERVICE_NAME_ENV),
        sampling_ratio=_sampling_ratio(),
        trust_inbound_context=_trusts_inbound_context(),
    )


def _named(variable: str) -> str:
    return os.environ.get(variable, "").strip()


def _refuse_any_of(variables: tuple[str, ...], because: str) -> None:
    said = [one for one in variables if _named(one)]
    if said:
        raise TelemetryMisconfigured(f"{', '.join(said)} set, {because}. Unset it.")


def _trusts_inbound_context() -> bool:
    said = _named(TRUST_INBOUND_CONTEXT_ENV).lower()
    if said in AFFIRMATIONS:
        return True
    if said in DENIALS:
        return False
    raise TelemetryMisconfigured(
        f"{TRUST_INBOUND_CONTEXT_ENV}={said!r} is neither yes nor no. Say 1 or 0."
    )


def _sampling_ratio() -> float:
    said = _named(SAMPLING_RATIO_ENV)
    if said == "":
        return EVERY_TRACE
    refusal = TelemetryMisconfigured(
        f"{SAMPLING_RATIO_ENV}={said!r} is not a ratio. Give a number from 0 to 1."
    )
    try:
        ratio = float(said)
    except ValueError as error:
        raise refusal from error
    if not 0.0 <= ratio <= 1.0:
        raise refusal
    return ratio


def build() -> Database:
    """The substrate this deployment gets, from the environment."""
    if os.environ.get(OWNER_URL_ENV):
        raise OwnerCredentialVisible(
            f"{OWNER_URL_ENV} is set in this process. The application never applies DDL, and "
            f"a web process holding a credential that can is the thing the two-role split "
            f"exists to prevent. Run `make migrate` as a release step and start the "
            f"application without it -- `env -u {OWNER_URL_ENV} ...` if they share a shell."
        )
    dsn = os.environ.get(DATABASE_URL_ENV)
    if dsn is None or dsn.strip() == "":
        return MemoryDatabase(seed_tenant=SENTINEL_TENANT)
    from app.store.pg import PostgresDatabase

    return PostgresDatabase(dsn=dsn, schema=os.environ.get(SCHEMA_ENV))
