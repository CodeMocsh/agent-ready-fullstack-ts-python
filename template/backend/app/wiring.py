"""What this deployment configured: which substrate this process gets, which frontend, and
where its telemetry goes. Everything here reads the environment and builds from it.

Two questions live elsewhere. `app/deployment.py` names the variables and, with the project's
`app/environment.py`, says whether this configuration is legitimate at all; `app/lifespan.py`
runs what this builds and reads nothing. `docs/adr/template/0014`.

**No `DATABASE_URL` means the in-memory substrate**, which only the development loop may run:
`refuse_development_settings` refuses it in production. Nothing here degrades from Postgres to
memory on an error. A malformed `DATABASE_URL` fails at boot.

**And the application refuses to hold the owner credential.** Seeing `DATABASE_OWNER_URL` is a
refusal rather than a warning. A single container that migrates and then serves drops it
between the two -- `env -u DATABASE_OWNER_URL uvicorn`.
"""

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import unquote

from app.deployment import (
    ACKNOWLEDGED_ENV,
    ACQUIRE_TIMEOUT_ENV,
    BUNDLE_ENV,
    DATABASE_URL_ENV,
    IDLE_IN_TRANSACTION_TIMEOUT_ENV,
    NEEDS_THE_ENDPOINT,
    NOT_READ,
    OTLP_ENDPOINT_ENV,
    PROTOCOL_ENV,
    RESOURCE_ATTRIBUTES_ENV,
    SAMPLING_RATIO_ENV,
    SEMCONV_ENV,
    SERVICE_NAME_ENV,
    STABLE_SEMCONV,
    STATEMENT_TIMEOUT_ENV,
    TIMEOUTS_ENV,
    TRUST_INBOUND_CONTEXT_ENV,
    stated,
)
from app.identity import SENTINEL_TENANT
from app.migrate import OWNER_URL_ENV
from app.store import Database
from app.store.conn import SCHEMA_ENV
from app.store.memory import MemoryDatabase
from app.store.pg import SMALLEST_BOUND, TIMEOUTS, PostgresDatabase, Timeouts

DENIALS: Final = frozenset({"", "0", "false", "no", "off"})
"""Spellings of "no" that must not read as an acknowledgement.

Any non-empty value counting would make `UNAUTHENTICATED_IS_INTENTIONAL=0` silence the
warning, which is the opposite of what somebody typing it meant -- and the only way they
would find out is by not being told.
"""


def unauthenticated_is_acknowledged() -> bool:
    """Whether this deployment has said out loud that it serves everybody on purpose."""
    return os.environ.get(ACKNOWLEDGED_ENV, "").strip().lower() not in DENIALS


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


class TimeoutsMisconfigured(RuntimeError):
    """A bound on a wait on Postgres is set to something this process will not run with, so it
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


SERVICE_VERSION: Final = "service.version"
CLOUD_PROVIDER: Final = "cloud.provider"
CLOUD_ACCOUNT: Final = "cloud.account.id"


def build_service_version() -> str | None:
    """The `service.version` in `OTEL_RESOURCE_ATTRIBUTES`, decoded as the SDK decodes it, or
    `None` when the deployment names none. Read whether or not telemetry is on. Refuses to start
    on an entry that is not `key=value`."""
    return _resource_attributes().get(SERVICE_VERSION)


def build_gcp_project() -> str | None:
    """The GCP project Cloud Logging finds each line's trace in: `cloud.account.id` in
    `OTEL_RESOURCE_ATTRIBUTES` when `cloud.provider` there is `gcp`, and `None` for any other
    provider or none. Refuses to start on `gcp` with no project, or on an entry that is not
    `key=value`."""
    attributes = _resource_attributes()
    if attributes.get(CLOUD_PROVIDER) != "gcp":
        return None
    project = attributes.get(CLOUD_ACCOUNT, "")
    if project == "":
        raise TelemetryMisconfigured(
            f"{RESOURCE_ATTRIBUTES_ENV} says {CLOUD_PROVIDER}=gcp and names no {CLOUD_ACCOUNT}, "
            f"so no log line can name its trace in Cloud Logging. Add "
            f"{CLOUD_ACCOUNT}=<the project id>."
        )
    return project


def _resource_attributes() -> dict[str, str]:
    said = stated(RESOURCE_ATTRIBUTES_ENV)
    if said == "":
        return {}
    attributes: dict[str, str] = {}
    for entry in said.split(","):
        key, equals, value = entry.partition("=")
        if equals == "" or key.strip() == "":
            raise TelemetryMisconfigured(
                f"{RESOURCE_ATTRIBUTES_ENV} holds {entry.strip()!r}, which is not key=value. "
                f"Separate entries with commas and nothing else."
            )
        attributes[key.strip()] = unquote(value.strip())
    return attributes


def build_telemetry() -> TelemetrySettings | None:
    """Telemetry, when `OTEL_EXPORTER_OTLP_ENDPOINT` names a Collector over HTTP; otherwise
    `None`, and nothing is instrumented.

    Refuses to start on anything it would not act on: without the endpoint, a variable from
    `NEEDS_THE_ENDPOINT`; with it, one from `NOT_READ`, a protocol other than `http/protobuf`,
    an endpoint that is not a URL, an unparsable ratio or trust flag, or a semantic-convention
    choice other than `STABLE_SEMCONV`.
    """
    endpoint = stated(OTLP_ENDPOINT_ENV).rstrip("/")
    if endpoint == "":
        _refuse_any_of(
            NEEDS_THE_ENDPOINT, f"and {OTLP_ENDPOINT_ENV} unset: nothing would be exported"
        )
        return None
    _refuse_any_of(NOT_READ, "and this process does not read it")
    if stated(PROTOCOL_ENV) not in ("", "http/protobuf"):
        raise TelemetryMisconfigured(
            f"{PROTOCOL_ENV}={stated(PROTOCOL_ENV)!r}: this process exports over http/protobuf "
            f"only. Point it at the Collector's HTTP port."
        )
    if stated(SEMCONV_ENV) not in ("", STABLE_SEMCONV):
        raise TelemetryMisconfigured(
            f"{SEMCONV_ENV}={stated(SEMCONV_ENV)!r}: the declared attributes are the stable "
            f"conventions' names, so this process sets {STABLE_SEMCONV!r} itself. Unset it."
        )
    if not endpoint.startswith(("http://", "https://")):
        raise TelemetryMisconfigured(
            f"{OTLP_ENDPOINT_ENV}={endpoint!r} is not an http:// or https:// URL."
        )
    if stated(SERVICE_NAME_ENV) == "":
        raise TelemetryMisconfigured(
            f"{OTLP_ENDPOINT_ENV} is set and {SERVICE_NAME_ENV} is not. Every span and metric "
            f"is filed under the service name, so name this one."
        )
    return TelemetrySettings(
        endpoint=endpoint,
        service=stated(SERVICE_NAME_ENV),
        sampling_ratio=_sampling_ratio(),
        trust_inbound_context=_trusts_inbound_context(),
    )


def _refuse_any_of(variables: tuple[str, ...], because: str) -> None:
    said = [one for one in variables if stated(one)]
    if said:
        raise TelemetryMisconfigured(f"{', '.join(said)} set, {because}. Unset it.")


def _trusts_inbound_context() -> bool:
    said = stated(TRUST_INBOUND_CONTEXT_ENV).lower()
    if said in AFFIRMATIONS:
        return True
    if said in DENIALS:
        return False
    raise TelemetryMisconfigured(
        f"{TRUST_INBOUND_CONTEXT_ENV}={said!r} is neither yes nor no. Say 1 or 0."
    )


def _sampling_ratio() -> float:
    said = stated(SAMPLING_RATIO_ENV)
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


def build_timeouts() -> Timeouts:
    """The bounds on every wait on Postgres: `TIMEOUTS`, with each one a deployment set replaced
    by its value in seconds. Raises `TimeoutsMisconfigured` on a value that is not a number of
    seconds of at least `SMALLEST_BOUND`."""
    return Timeouts(
        statement=_seconds(STATEMENT_TIMEOUT_ENV, TIMEOUTS.statement),
        idle_in_transaction=_seconds(IDLE_IN_TRANSACTION_TIMEOUT_ENV, TIMEOUTS.idle_in_transaction),
        acquire=_seconds(ACQUIRE_TIMEOUT_ENV, TIMEOUTS.acquire),
    )


def _seconds(variable: str, shipped: float) -> float:
    said = stated(variable)
    if said == "":
        return shipped
    refusal = TimeoutsMisconfigured(
        f"{variable}={said!r} is not a bound. Give a number of seconds of at least "
        f"{SMALLEST_BOUND}, or unset it for {shipped}."
    )
    try:
        seconds = float(said)
    except ValueError as error:
        raise refusal from error
    if not (math.isfinite(seconds) and seconds >= SMALLEST_BOUND):
        raise refusal
    return seconds


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
        said = [one for one in TIMEOUTS_ENV if stated(one)]
        if said:
            raise TimeoutsMisconfigured(
                f"{', '.join(said)} set and {DATABASE_URL_ENV} unset: the in-memory substrate "
                f"has no wait to bound. Unset it, or set {DATABASE_URL_ENV}."
            )
        return MemoryDatabase(seed_tenant=SENTINEL_TENANT)
    return PostgresDatabase(dsn=dsn, schema=os.environ.get(SCHEMA_ENV), timeouts=build_timeouts())
