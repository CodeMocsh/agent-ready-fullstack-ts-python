"""Every line this process writes to its log, declared once.

Each line is one JSON object on stdout, with `time` in UTC, `severity`, `message`, `logger`, and
`request_id` while a request is being served. A traceback goes whole into `exception`. Those
names are what Cloud Logging, CloudWatch and Azure Monitor each parse without an agent or an SDK;
the HTTP fields follow the OpenTelemetry semantic conventions.

**This module and `app/log_lines.py` are the only ones that may import `logging` or
`structlog`**, and ruff refuses either anywhere else in the backend but `tests/log/`. This one
holds the template's lines; the project's own are in `app/log_lines.py`. Every field a line can
carry is a parameter of a function in one of the two, or, on `request completed`, a field of
`TEMPLATE_FIELDS` or of `app/request_line.py`. Records from libraries pass through the same
formatter with the message they wrote, and are heard only at `WARNING` and above.
`docs/adr/template/0011` holds the reasoning.
"""

import logging
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Final, TextIO, final, override

import structlog
from asgi_correlation_id import CorrelationIdMiddleware, correlation_id
from fastapi import FastAPI, Request, Response
from fastapi.routing import iter_route_contexts
from opentelemetry import trace
from starlette.routing import Match
from structlog.typing import EventDict, Processor, WrappedLogger

from app.deployment import ACKNOWLEDGED_ENV
from app.identity.shared import Principal
from app.models import ClientEvent
from app.refusal import Bound
from app.request_line import FIELDS as PROJECT_FIELDS

_LOG: Final = structlog.stdlib.get_logger("app")

TENANT_ID: Final = "tenant_id"
"""The field naming the tenant a request resolved to, on every `request completed` line."""

ACTOR_ISSUER: Final = "actor.issuer"
ACTOR_SUBJECT: Final = "actor.subject"

TEMPLATE_FIELDS: Final = (TENANT_ID, ACTOR_ISSUER, ACTOR_SUBJECT)
"""The fields `name_principal` names on `request completed`, before the project's own. They
never include the actor's name."""


class UndeclaredRequestField(RuntimeError):
    """A field `app/request_line.py` does not declare was named on a request."""


def configure(service_version: str | None) -> None:
    """Send every record in this process to stdout as one JSON line, from here on, each naming
    `service_version` under `service.version` -- `None` when the deployment named none.

    Idempotent, and it touches only what it installed: a handler from an earlier call is
    replaced, and any other handler on the root logger is left alone. Below `WARNING` only
    `_SAYS_INFO` is heard. uvicorn's access log is off; `request completed` replaces it. Call it
    after uvicorn has configured its own loggers and before it serves -- `assemble` does.
    """
    handler = _Stdout()
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=_ENRICHED,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
                _naming_version(service_version),
                _named_for_every_cloud,
                structlog.processors.JSONRenderer(),
            ],
        )
    )
    root = logging.getLogger()
    for installed in [one for one in root.handlers if isinstance(one, _Stdout)]:
        root.removeHandler(installed)
    root.addHandler(handler)
    root.setLevel(logging.WARNING)
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
    for name in _SAYS_INFO:
        logging.getLogger(name).setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").handlers.clear()
    logging.getLogger("uvicorn.access").propagate = False
    structlog.configure(
        processors=[*_ENRICHED, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def instrument(app: FastAPI) -> None:
    """Give every request an id and one `request completed` line.

    The id is the `X-Request-ID` the client sent when it is a uuid, and a fresh one otherwise;
    either way it is echoed on the response. Added in this order so the id is bound before the
    request is timed.
    """
    app.middleware("http")(_completed)
    app.add_middleware(CorrelationIdMiddleware)


def serving(substrate: str, schema: str) -> None:
    _LOG.info("serving", substrate=substrate, schema=schema)


def connections_budgeted(
    *,
    max_connections: int,
    reserved: int,
    outside: dict[str, int],
    instances: int,
    in_process: dict[str, int],
    pool: int,
) -> None:
    """The connection budget the pool opened under. `docs/adr/template/0022`."""
    _LOG.info(
        "connections budgeted",
        max_connections=max_connections,
        reserved=reserved,
        outside=outside,
        instances=instances,
        in_process=in_process,
        pool=pool,
    )


def admission(
    *, in_flight: int, per_second: float, burst: int, streams: int, enforced: bool
) -> None:
    """What this process admits for one actor, and whether it refuses a request over a bound. At
    `WARNING` when it only reports one. `docs/adr/template/0023`."""
    fields = {
        "in_flight": in_flight,
        "per_second": per_second,
        "burst": burst,
        "streams": streams,
        "enforced": enforced,
    }
    if enforced:
        _LOG.info("admission", **fields)
    else:
        _LOG.warning("admission", **fields)


def admission_refused(bound: Bound) -> None:
    """A request over `bound` was refused with a `429`."""
    _LOG.info("admission refused", bound=bound)


def admission_would_refuse(bound: Bound) -> None:
    """A request over `bound` was admitted outside the count, because admission only reports."""
    _LOG.info("admission would refuse", bound=bound)


def database_busy(refusal: str) -> None:
    """A request got no database connection, and is refused with a `503`. `refusal` names the
    class of the failure."""
    _LOG.warning("database busy", refusal=refusal)


def identity_verified() -> None:
    _LOG.info("identity: a credential is verified on every request")


def identity_open_on_purpose(tenant: str) -> None:
    _LOG.info(_IDENTITY_OPEN_ON_PURPOSE, tenant=tenant)


def identity_open(tenant: str, substrate: str) -> None:
    _LOG.warning(_IDENTITY_OPEN, tenant=tenant, substrate=substrate)


def name_principal(request: Request, principal: Principal) -> None:
    """Put the principal's tenant and actor on this request's `request completed` line, under
    `TEMPLATE_FIELDS`. Raises `AttributeError` for a request `instrument` is not serving.
    `docs/adr/template/0011`.
    """
    request.state.request_line.update(
        {
            TENANT_ID: principal.tenant_id,
            ACTOR_ISSUER: principal.actor.issuer,
            ACTOR_SUBJECT: principal.actor.subject,
        }
    )


def name_on_request_line(request: Request, field: str, value: str) -> None:
    """Put `value` under `field` on this request's `request completed` line.

    Raises `UndeclaredRequestField` for a field `app/request_line.py` does not declare, a field
    of `TEMPLATE_FIELDS` included: `name_principal` names those together. Raises
    `AttributeError` for a request `instrument` is not serving. `docs/adr/template/0011`.
    """
    if field in TEMPLATE_FIELDS:
        raise UndeclaredRequestField(
            f"log.name_principal names {field!r} with the rest of the principal. Call it instead."
        )
    if field not in PROJECT_FIELDS:
        raise UndeclaredRequestField(
            f"{field!r} is not a field of request completed. Declare it in FIELDS in "
            f"app/request_line.py."
        )
    request.state.request_line[field] = value


def request_line_fields() -> tuple[str, ...]:
    """Every field `request completed` carries, in the order it carries them."""
    return (*TEMPLATE_FIELDS, *sorted(PROJECT_FIELDS))


def request_completed(
    method: str, route: str | None, status: int, duration_ms: float, named: Mapping[str, str]
) -> None:
    """One request, by the route that answered it -- never by its path, which carries values.
    Every field of `request_line_fields` is on it, `None` when the request did not name it: no
    principal on a public route, or when the identity seam refused the request."""
    _LOG.info(
        "request completed",
        **{
            "http.request.method": method,
            "http.route": route,
            "http.response.status_code": status,
            "duration_ms": duration_ms,
        },
        **{field: named.get(field) for field in request_line_fields()},
    )


def client_event(event: ClientEvent) -> None:
    """A failure the browser reported, marked `source=client`. Anybody can post one: read it as
    a report and never as evidence of what the server did."""
    _LOG.warning(
        "client event",
        source="client",
        kind=event.kind,
        route=event.route,
        error=event.error,
        status=event.status,
        failed_request_id=event.request_id,
    )


def span_attribute_dropped(attribute: str) -> None:
    """A span attribute `app.telemetry` did not declare, named once. Never its value."""
    _LOG.info("span attribute dropped", attribute=attribute)


def loop_held(
    cpu_seconds: float, late_seconds: float, function: str | None, line: int | None
) -> None:
    """The event loop's thread used `cpu_seconds` of CPU between two ticks of `app.loop_watch`,
    and the second tick ran `late_seconds` late. `function` is a module and a qualified name, and
    `line` a line in it, where the loop was while the tick was overdue: `None` for both when
    nothing looked. Never a path, a value or a message."""
    _LOG.warning(
        "event loop held",
        cpu_ms=round(cpu_seconds * 1000, 1),
        late_ms=round(late_seconds * 1000, 1),
        **{"code.function.name": function, "code.line.number": line},
    )


def beside_failed(work: str, error: BaseException) -> None:
    """The task `app.waits.beside` started under the name `work` raised `error`. The traceback
    goes whole into `exception`."""
    _LOG.error("work beside the requests failed", work=work, exc_info=error)


def beside_cancelled(work: str) -> None:
    """The task `app.waits.beside` started under the name `work` was cancelled before it ended, as
    at shutdown."""
    _LOG.warning("work beside the requests cancelled", work=work)


def shared_failed(work: str, error: BaseException) -> None:
    """A run of the shared work `app.waits.SharedWork` named `work` raised `error`. The traceback
    goes whole into `exception`."""
    _LOG.error("shared work failed", work=work, exc_info=error)


def shared_cancelled(work: str) -> None:
    """A run of the shared work `app.waits.SharedWork` named `work` was cancelled before it ended,
    as at shutdown."""
    _LOG.warning("shared work cancelled", work=work)


def working(*, substrate: str, schema: str, kinds: tuple[str, ...], at_once: int) -> None:
    """The worker process claims the jobs of `kinds` from the schema `schema`, `at_once` at a
    time."""
    _LOG.info("working", substrate=substrate, schema=schema, kinds=list(kinds), at_once=at_once)


def job_done(*, kind: str, job: str, attempt: int) -> None:
    """Attempt `attempt` of the job `job` of `kind` ended the job with an outcome."""
    _LOG.info("job done", kind=kind, job=job, attempt=attempt)


def job_failed(*, kind: str, job: str, attempt: int, error: BaseException) -> None:
    """Attempt `attempt` of the job `job` of `kind` raised `error`. The traceback goes whole into
    `exception`."""
    _LOG.warning("job failed", kind=kind, job=job, attempt=attempt, exc_info=error)


def job_dead(*, kind: str, job: str, attempts: int, failure: str) -> None:
    """The job `job` of `kind` failed on each of its `attempts` attempts, and runs no more.
    `failure` names the class of the last error."""
    _LOG.error("job dead", kind=kind, job=job, attempts=attempts, failure=failure)


def lease_lost(*, kind: str, job: str, attempt: int) -> None:
    """Attempt `attempt` of the job `job` of `kind` stopped, because its lease ended or another
    worker claimed the job. It wrote nothing after that."""
    _LOG.warning("lease lost", kind=kind, job=job, attempt=attempt)


_IDENTITY_OPEN_ON_PURPOSE: Final = (
    f"identity: none, and {ACKNOWLEDGED_ENV} says that is deliberate -- every request is "
    f"served as one tenant"
)

_IDENTITY_OPEN: Final = (
    f"identity: this deployment authenticates nothing. A request carrying no credential "
    f"resolves to a principal, so anyone who can reach this process can read and write "
    f"everything it holds. Replace principal_for() in app/identity/__init__.py -- or set "
    f"{ACKNOWLEDGED_ENV}=1 to record that serving everybody is deliberate and see this as INFO."
)


@final
class _Stdout(logging.StreamHandler[TextIO]):
    """The handler `configure` installs, told apart from every other one on the root logger.

    It writes to `sys.stdout` as it is when a record arrives rather than as it was when the
    handler was built, so whatever replaced it since -- a test's capture -- gets the line.
    """

    @override
    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stdout
        super().emit(record)


def current_request_id() -> str | None:
    """The id of the request being served, and `None` outside one."""
    return correlation_id.get()


def current_trace_id() -> str | None:
    """The id of the sampled trace this code runs in, and `None` outside one."""
    span = trace.get_current_span().get_span_context()
    return format(span.trace_id, "032x") if span.is_valid and span.trace_flags.sampled else None


def _with_request_id(_logger: WrappedLogger, _method: str, line: EventDict) -> EventDict:
    request_id = current_request_id()
    if request_id is not None:
        line["request_id"] = request_id
    return line


def _with_trace(_logger: WrappedLogger, _method: str, line: EventDict) -> EventDict:
    span = trace.get_current_span().get_span_context()
    if span.is_valid and span.trace_flags.sampled:
        line["trace_id"] = format(span.trace_id, "032x")
        line["span_id"] = format(span.span_id, "016x")
    return line


def _naming_version(version: str | None) -> Processor:
    def named(_logger: WrappedLogger, _method: str, line: EventDict) -> EventDict:
        line["service.version"] = version
        return line

    return named


def _named_for_every_cloud(_logger: WrappedLogger, _method: str, line: EventDict) -> EventDict:
    line["severity"] = line.pop("level").upper()
    line["message"] = line.pop("event")
    return line


_SAYS_INFO: Final = ("app", "uvicorn.error")
"""The loggers heard at `INFO`: this module's own, and uvicorn's start and stop."""

_ENRICHED: Final[list[Processor]] = [
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.processors.TimeStamper(fmt="iso", utc=True, key="time"),
    _with_request_id,
    _with_trace,
]


def route_answering(request: Request) -> str | None:
    """The whole template of the route `request` reaches, every prefix it was included under
    first, or `None` when no route answers it. Matched as the OpenTelemetry instrumentation
    matches it, so the line and the server span name one route."""
    partial: str | None = None
    for route in iter_route_contexts(request.app.routes):
        match, _ = route.matches(request.scope)
        if match is Match.FULL:
            return route.path
        if match is Match.PARTIAL and partial is None:
            partial = route.path
    return partial


async def _completed(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Times the request and logs it. A request that raises is logged as a `500`, which is what
    the server answers once the exception has passed through here."""
    started = time.perf_counter()
    route = route_answering(request)
    status = 500
    named: dict[str, str] = {}
    request.state.request_line = named
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        request_completed(
            request.method,
            route,
            status,
            round((time.perf_counter() - started) * 1000, 1),
            named,
        )


def response_too_large(*, route: str | None, limit: int, observed: int, reason: str) -> None:
    """A finite response was refused before its bytes left the process."""
    _LOG.error(
        "response too large",
        route=route,
        limit=limit,
        observed=observed,
        reason=reason,
        refusal="ResponseTooLarge",
    )


def response_timed_out(*, route: str | None, seconds: float) -> None:
    """A finite response did not finish buffering before its deadline."""
    _LOG.error("response timed out", route=route, seconds=seconds, refusal="ResponseTimedOut")
