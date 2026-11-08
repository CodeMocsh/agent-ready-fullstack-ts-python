"""One request event for each request a principal made, recorded before the response leaves.

A request that `audit_events.serve` gave a principal records one event, whatever it answered: a
refusal, a `404` and a `500` included. A request with no principal records none: a public route,
and a request the identity seam refused. A request cancelled before it answered, as at shutdown,
records none, and so does a request that the database did not serve: its event would wait for the
database too. When the database does not serve the event itself, the request answers the `503` of
`NoConnectionFree` in place of its own response. When the event cannot be recorded, the request fails
with a `500`, and the response it would have sent does not leave. `docs/adr/template/0017`.
"""

from fastapi import FastAPI, Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import log
from app.audit_events import SERVED, UNRECORDED, RequestEvent
from app.deps import database_of
from app.identity.shared import Principal
from app.refusal import no_connection_free
from app.store import DatabaseUnavailable

FAILED = 500
"""The status a request event names for a request whose handler raised."""


def instrument(app: FastAPI) -> None:
    """Record a request event for every request a principal makes to `app`.

    Call it before `log.instrument`, so the request id and the request line are in place around
    it.
    """
    app.add_middleware(RecordsRequests)


async def record(request: Request, status: int) -> None:
    """Record the request event for `request`, answered with `status`, when a principal made it
    and nothing called `unrecorded` on it."""
    principal: Principal | None = getattr(request.state, SERVED, None)
    if principal is None or getattr(request.state, UNRECORDED, False):
        return
    route = log.route_answering(request)
    if route is None:
        raise RuntimeError(f"{request.method} {request.url.path} served a principal and no route")
    event = RequestEvent(
        actor=principal.actor,
        method=request.method,
        route=route,
        resource_ids=tuple(str(value) for value in request.path_params.values()),
        status=status,
        request_id=log.current_request_id(),
        trace_id=log.current_trace_id(),
    )
    await database_of(request).audit_events(principal.tenant_id).record(event)


class RecordsRequests:
    """The middleware `instrument` adds."""

    def __init__(self, app: ASGIApp) -> None:
        self._app: ASGIApp = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        recording = _Recording(scope, receive, send)
        try:
            await self._app(scope, receive, recording.send)
        finally:
            await recording.end()


class _Recording:
    """One request's response, held at its start until its request event is recorded."""

    def __init__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self._scope: Scope = scope
        self._receive: Receive = receive
        self._send: Send = send
        self._request: Request = Request(scope)
        self._answered: bool = False
        self._refused: bool = False

    async def send(self, message: Message) -> None:
        if self._refused:
            return
        if message["type"] == "http.response.start":
            self._answered = True
            if not await self._recorded(message["status"]):
                return
        await self._send(message)

    async def end(self) -> None:
        if not self._answered and not log.cancelling():
            _ = await self._recorded(FAILED)

    async def _recorded(self, status: int) -> bool:
        """Whether the event was recorded. When the database did not serve it, the `503` of
        `NoConnectionFree` goes out in place of the response."""
        try:
            await record(self._request, status)
        except DatabaseUnavailable as unavailable:
            self._refused = True
            log.database_unavailable(type(unavailable).__name__, unavailable.cause)
            refused = no_connection_free(unavailable.retry_after)
            await refused(self._scope, self._receive, self._send)
        return not self._refused
