"""Every line this process writes to its log, declared once.

Each line is one JSON object on stdout, with `time` in UTC, `severity`, `message`, `logger`, and
`request_id` while a request is being served. A traceback goes whole into `exception`. Those
names are what Cloud Logging, CloudWatch and Azure Monitor each parse without an agent or an SDK;
the HTTP fields follow the OpenTelemetry semantic conventions.

**This is the only module that may import `logging` or `structlog`**, and ruff refuses either
anywhere else in the backend but `tests/log/`. Every field a line can carry is a parameter of a
function below. Records from libraries pass through the same formatter with the message they
wrote, and are heard only at `WARNING` and above. `docs/adr/0009` holds the reasoning.
"""

import logging
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Final, TextIO, final, override

import structlog
from asgi_correlation_id import CorrelationIdMiddleware, correlation_id
from fastapi import FastAPI, Request, Response
from structlog.typing import EventDict, Processor, WrappedLogger

from app.models import ClientEvent
from app.wiring import ACKNOWLEDGED_ENV

_LOG: Final = structlog.stdlib.get_logger("app")


def configure() -> None:
    """Send every record in this process to stdout as one JSON line, from here on.

    Idempotent, and it touches only what it installed: a handler from an earlier call is
    replaced, and any other handler on the root logger is left alone. Below `WARNING` only
    `_SAYS_INFO` is heard. uvicorn's access log is off; `request completed` replaces it. Call it
    after uvicorn has configured its own loggers and before it serves -- `create_app` does.
    """
    handler = _Stdout()
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=_ENRICHED,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
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


def identity_verified() -> None:
    _LOG.info("identity: a credential is verified on every request")


def identity_open_on_purpose(tenant: str) -> None:
    _LOG.info(_IDENTITY_OPEN_ON_PURPOSE, tenant=tenant)


def identity_open(tenant: str, substrate: str) -> None:
    _LOG.warning(_IDENTITY_OPEN, tenant=tenant, substrate=substrate)


def request_completed(method: str, route: str | None, status: int, duration_ms: float) -> None:
    """One request, by the route that answered it -- never by its path, which carries values."""
    _LOG.info(
        "request completed",
        **{
            "http.request.method": method,
            "http.route": route,
            "http.response.status_code": status,
            "duration_ms": duration_ms,
        },
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


_IDENTITY_OPEN_ON_PURPOSE: Final = (
    f"identity: none, and {ACKNOWLEDGED_ENV} says that is deliberate -- every request is "
    f"served as one tenant"
)

_IDENTITY_OPEN: Final = (
    f"identity: this deployment authenticates nothing. A request carrying no credential "
    f"resolves to a tenant, so anyone who can reach this process can read and write everything "
    f"it holds. Replace tenant_for() in app/identity.py -- or set {ACKNOWLEDGED_ENV}=1 to "
    f"record that serving everybody is deliberate and see this as INFO."
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


def _with_request_id(_logger: WrappedLogger, _method: str, line: EventDict) -> EventDict:
    request_id = correlation_id.get()
    if request_id is not None:
        line["request_id"] = request_id
    return line


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
]


async def _completed(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Times the request and logs it. A request that raises is logged as a `500`, which is what
    the server answers once the exception has passed through here."""
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        request_completed(
            request.method,
            getattr(request.scope.get("route"), "path_format", None),
            status,
            round((time.perf_counter() - started) * 1000, 1),
        )
