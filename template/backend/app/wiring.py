"""What this deployment configured: which substrate this process gets, which frontend, and
where its telemetry goes. Everything here reads the environment and builds from it.

Two questions live elsewhere. `app/environment.py` names the variables and says whether this
configuration is legitimate at all; `app/lifespan.py` runs what this builds and reads nothing.
`docs/adr/0014`.

**No `DATABASE_URL` means the in-memory substrate**, which only the development loop may run:
`refuse_development_settings` refuses it in production. Nothing here degrades from Postgres to
memory on an error. A malformed `DATABASE_URL` fails at boot.

**And the application refuses to hold the owner credential.** Seeing `DATABASE_OWNER_URL` is a
refusal rather than a warning. A single container that migrates and then serves drops it
between the two -- `env -u DATABASE_OWNER_URL uvicorn`.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from app.environment import (
    ACKNOWLEDGED_ENV,
    BUNDLE_ENV,
    DATABASE_URL_ENV,
    NEEDS_THE_ENDPOINT,
    NOT_READ,
    OTLP_ENDPOINT_ENV,
    PROTOCOL_ENV,
    SAMPLING_RATIO_ENV,
    SEMCONV_ENV,
    SERVICE_NAME_ENV,
    STABLE_SEMCONV,
    TRUST_INBOUND_CONTEXT_ENV,
    stated,
)
from app.identity import SENTINEL_TENANT
from app.migrate import OWNER_URL_ENV
from app.store import Database
from app.store.conn import SCHEMA_ENV
from app.store.memory import MemoryDatabase

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
