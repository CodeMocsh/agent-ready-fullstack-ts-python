"""What this deployment configured: which substrate this process gets, which frontend, and
where its telemetry goes. Everything here reads the environment and builds from it.

Two questions live elsewhere. `app/deployment.py` names the variables and, with the project's
`app/environment.py`, says whether this configuration is legitimate at all; `app/lifespan.py`
runs what this builds and reads nothing. `docs/adr/template/0010`.

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

from app import admission_limits, connection_budget, jobs, read_limits, reads, response_limits
from app.admission import Limits, Policy
from app.deployment import (
    ACKNOWLEDGED_ENV,
    ACQUIRE_TIMEOUT_ENV,
    ADMISSION_BURST_ENV,
    ADMISSION_IN_FLIGHT_ENV,
    ADMISSION_MODE_ENV,
    ADMISSION_PER_SECOND_ENV,
    ADMISSION_STREAMS_ENV,
    BUNDLE_ENV,
    DATABASE_URL_ENV,
    IDLE_IN_TRANSACTION_TIMEOUT_ENV,
    INSTANCES_ENV,
    NEEDS_THE_ENDPOINT,
    NOT_READ,
    OTLP_ENDPOINT_ENV,
    PROTOCOL_ENV,
    READS_MAX_PARTS_ENV,
    READS_PART_BOUND_SECONDS_ENV,
    READS_PART_CONCURRENCY_ENV,
    RESOURCE_ATTRIBUTES_ENV,
    RESPONSE_CEILING_BYTES_ENV,
    RESPONSE_MAX_BYTES_ENV,
    SAMPLING_RATIO_ENV,
    SEMCONV_ENV,
    SERVICE_NAME_ENV,
    STABLE_SEMCONV,
    STATEMENT_TIMEOUT_ENV,
    TIMEOUTS_ENV,
    TRUST_INBOUND_CONTEXT_ENV,
    in_development,
    stated,
)
from app.identity.shared import SENTINEL_TENANT
from app.migrate import OWNER_URL_ENV
from app.store import Database
from app.store.budget import TEMPLATE_IN_PROCESS, Draws
from app.store.conn import SCHEMA_ENV
from app.store.memory import MemoryDatabase
from app.store.pg import SMALLEST_BOUND, TIMEOUTS, PostgresDatabase, Timeouts
from app.waits import REQUEST_BUDGET_SECONDS

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


class AdmissionMisconfigured(RuntimeError):
    """A variable of admission is set to something this process will not run with, so it
    refuses to start."""


class ReadsMisconfigured(RuntimeError):
    """A variable of `GET /reads` is set to something this process will not run with, or its
    numbers do not fit the request budget or the burst of admission, so it refuses to start."""


class InstancesMisconfigured(RuntimeError):
    """`DATABASE_INSTANCES` is missing in production, or is not a number of processes, so the
    process refuses to start."""


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


def build_service_version() -> str | None:
    """The `service.version` in `OTEL_RESOURCE_ATTRIBUTES`, decoded as the SDK decodes it, or
    `None` when the deployment names none. Read whether or not telemetry is on. Refuses to start
    on an entry that is not `key=value`."""
    said = stated(RESOURCE_ATTRIBUTES_ENV)
    if said == "":
        return None
    attributes: dict[str, str] = {}
    for entry in said.split(","):
        key, equals, value = entry.partition("=")
        if equals == "" or key.strip() == "":
            raise TelemetryMisconfigured(
                f"{RESOURCE_ATTRIBUTES_ENV} holds {entry.strip()!r}, which is not key=value. "
                f"Separate entries with commas and nothing else."
            )
        attributes[key.strip()] = unquote(value.strip())
    return attributes.get(SERVICE_VERSION)


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


def build_timeouts(*, serves_requests: bool = True) -> Timeouts:
    """The bounds on every wait on Postgres: `TIMEOUTS`, with each one a deployment set replaced
    by its value in seconds. Raises `TimeoutsMisconfigured` on a value that is not a number of
    seconds of at least `SMALLEST_BOUND`. In a process that serves requests, it also raises on a
    bound that makes a request wait on Postgres for as long as `REQUEST_BUDGET_SECONDS`: the
    answer to a statement, or a connection. The worker serves none."""
    timeouts = Timeouts(
        statement=_seconds(STATEMENT_TIMEOUT_ENV, TIMEOUTS.statement),
        idle_in_transaction=_seconds(IDLE_IN_TRANSACTION_TIMEOUT_ENV, TIMEOUTS.idle_in_transaction),
        acquire=_seconds(ACQUIRE_TIMEOUT_ENV, TIMEOUTS.acquire),
    )
    if not serves_requests:
        return timeouts
    for variable, wait, seconds in (
        (STATEMENT_TIMEOUT_ENV, "an answer", timeouts.answer),
        (ACQUIRE_TIMEOUT_ENV, "a connection", timeouts.acquire),
    ):
        if seconds >= REQUEST_BUDGET_SECONDS:
            raise TimeoutsMisconfigured(
                f"{variable}={stated(variable)!r} makes a request wait {seconds} s for {wait}, "
                f"and each wait in a request has a bound under {REQUEST_BUDGET_SECONDS} s. Run "
                f"long work as a job, in app/jobs.py: the worker process allows a longer bound."
            )
    return timeouts


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


ENFORCE: Final = "enforce"
EVALUATE: Final = "evaluate"


def build_admission() -> Policy:
    """What this process admits for one actor: `admission_limits.LIMITS`, with each field a
    deployment set replaced, and whether it refuses a request over a bound. Raises
    `AdmissionMisconfigured` on a count that is not a whole number of at least 1, a rate that is
    not a finite number above 0, and a mode other than `enforce` or `evaluate`."""
    shipped = admission_limits.LIMITS
    mode = stated(ADMISSION_MODE_ENV) or ENFORCE
    if mode not in (ENFORCE, EVALUATE):
        raise AdmissionMisconfigured(
            f"{ADMISSION_MODE_ENV}={mode!r}: say {ENFORCE!r} to refuse a request over a bound, or "
            f"{EVALUATE!r} to report it and admit it."
        )
    return Policy(
        limits=Limits(
            in_flight=_count(ADMISSION_IN_FLIGHT_ENV, shipped.in_flight),
            per_second=_rate(ADMISSION_PER_SECOND_ENV, shipped.per_second),
            burst=_count(ADMISSION_BURST_ENV, shipped.burst),
            streams=_count(ADMISSION_STREAMS_ENV, shipped.streams),
        ),
        enforced=mode == ENFORCE,
    )


def _count(
    variable: str, shipped: int, misconfigured: type[RuntimeError] = AdmissionMisconfigured
) -> int:
    said = stated(variable)
    if said == "":
        return shipped
    if not (said.isdecimal() and int(said) >= 1):
        raise misconfigured(
            f"{variable}={said!r} is not a count. Give a whole number of at least 1, or unset it "
            f"for {shipped}."
        )
    return int(said)


def build_reads(admitted: Limits) -> reads.Limits:
    """What one `GET /reads` runs: `read_limits.LIMITS`, with each field a deployment set
    replaced. Raises `ReadsMisconfigured` on a count that is not a whole number of at least 1, a
    bound that is not a finite number of seconds above 0, a group that may outlast the request
    budget, more parts than the `burst` that `admitted` gives one actor, and more parts at once
    than its `in_flight`."""
    shipped = read_limits.LIMITS
    bound = _part_bound(READS_PART_BOUND_SECONDS_ENV, shipped.part_bound_seconds)
    max_parts = _count(READS_MAX_PARTS_ENV, shipped.max_parts, ReadsMisconfigured)
    at_once = _count(READS_PART_CONCURRENCY_ENV, shipped.part_concurrency, ReadsMisconfigured)
    try:
        limits = reads.Limits(
            part_bound_seconds=bound, max_parts=max_parts, part_concurrency=at_once
        )
    except ValueError as error:
        raise ReadsMisconfigured(
            f"{READS_PART_BOUND_SECONDS_ENV}, {READS_MAX_PARTS_ENV} and "
            f"{READS_PART_CONCURRENCY_ENV} give {error}. Lower the bound or the most parts, or "
            f"raise the parts at once."
        ) from error
    if limits.max_parts > admitted.burst:
        raise ReadsMisconfigured(
            f"a group of {limits.max_parts} parts is more than the burst of {admitted.burst} that "
            f"admission gives one actor, so a whole group is never admitted. Lower "
            f"{READS_MAX_PARTS_ENV}, or raise {ADMISSION_BURST_ENV}."
        )
    if limits.part_concurrency > admitted.in_flight:
        raise ReadsMisconfigured(
            f"{limits.part_concurrency} parts at once is more than the {admitted.in_flight} "
            f"requests at once that admission gives one actor, so a group refuses its own parts. "
            f"Lower {READS_PART_CONCURRENCY_ENV}, or raise {ADMISSION_IN_FLIGHT_ENV}."
        )
    return limits


def _part_bound(variable: str, shipped: float) -> float:
    said = stated(variable)
    if said == "":
        return shipped
    refusal = ReadsMisconfigured(
        f"{variable}={said!r} is not a bound. Give a number of seconds above 0, or unset it for "
        f"{shipped}."
    )
    try:
        seconds = float(said)
    except ValueError as error:
        raise refusal from error
    if not (math.isfinite(seconds) and seconds > 0):
        raise refusal
    return seconds


def _rate(variable: str, shipped: float) -> float:
    said = stated(variable)
    if said == "":
        return shipped
    refusal = AdmissionMisconfigured(
        f"{variable}={said!r} is not a rate. Give a number of requests a second above 0, or "
        f"unset it for {shipped}."
    )
    try:
        rate = float(said)
    except ValueError as error:
        raise refusal from error
    if not (math.isfinite(rate) and rate > 0):
        raise refusal
    return rate


DEVELOPMENT_PROCESSES: Final = 2
"""The processes of the development loop on Postgres: the web process and the worker."""


def build_draws() -> Draws:
    """Everything that holds connections to the database: `DATABASE_INSTANCES` processes, each
    holding `TEMPLATE_IN_PROCESS` and `connection_budget.IN_PROCESS` beside the pool, and
    `connection_budget.OUTSIDE`.

    The development loop on Postgres is two processes, the web process and the worker, so there
    an unset `DATABASE_INSTANCES` is two. Raises
    `InstancesMisconfigured` when production leaves it unset, and on a value that is not a
    whole number of at least one.
    """
    said = stated(INSTANCES_ENV)
    if said == "" and not in_development():
        raise InstancesMisconfigured(
            f"{INSTANCES_ENV} is unset, and production sizes each pool from it. Set it to the "
            f"most processes that hold connections at once, the workers and the processes of a "
            f"revision that drains during a rollout included: twice the most instances of each, "
            f"when a rollout overlaps them all."
        )
    if said != "" and not (said.isdecimal() and int(said) >= 1):
        raise InstancesMisconfigured(
            f"{INSTANCES_ENV}={said!r} is not a number of processes. Give a whole number of at "
            f"least 1."
        )
    return Draws(
        instances=DEVELOPMENT_PROCESSES if said == "" else int(said),
        in_process={**TEMPLATE_IN_PROCESS, **connection_budget.IN_PROCESS},
        outside=connection_budget.OUTSIDE,
    )


def build(*, serves_requests: bool = True) -> Database:
    """The substrate this deployment gets, from the environment, running the jobs that
    `app/jobs.py` names. The worker serves no request, and passes `serves_requests=False` for
    the bounds of `build_timeouts`."""
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
        if stated(INSTANCES_ENV):
            raise InstancesMisconfigured(
                f"{INSTANCES_ENV} set and {DATABASE_URL_ENV} unset: the in-memory substrate "
                f"holds no connection. Unset it, or set {DATABASE_URL_ENV}."
            )
        return MemoryDatabase(seed_tenant=SENTINEL_TENANT, kinds=jobs.kinds)
    return PostgresDatabase(
        dsn=dsn,
        schema=os.environ.get(SCHEMA_ENV),
        draws=build_draws(),
        kinds=jobs.kinds,
        timeouts=build_timeouts(serves_requests=serves_requests),
    )


def build_response_limits() -> response_limits.Limits:
    """The default response limit and the largest finite route exception this deployment permits."""
    return response_limits.Limits(
        default=_response_bytes(RESPONSE_MAX_BYTES_ENV, response_limits.DEFAULT_BYTES),
        ceiling=_response_bytes(RESPONSE_CEILING_BYTES_ENV, response_limits.CEILING_BYTES),
    )


def _response_bytes(variable: str, shipped: int) -> int:
    said = stated(variable)
    if said == "":
        return shipped
    if not said.isdecimal():
        raise response_limits.ResponsePolicyInvalid(f"{variable} must be an integer byte count")
    return int(said)
