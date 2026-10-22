"""One request event for each request a principal made, recorded before the response leaves.

A request that `audit_events.serve` gave a principal records one event, whatever it answered: a
refusal, a `404` and a `500` included. A request with no principal records none: a public route,
and a request the identity seam refused. A request cancelled before it answered, as at shutdown,
records none. When the event cannot be recorded, the request fails
with a `500`, and the response it would have sent does not leave. `docs/adr/template/0017`.
"""

import asyncio

from fastapi import FastAPI, Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import log
from app.audit_events import SERVED, RequestEvent
from app.deps import database_of
from app.identity.shared import Principal

FAILED = 500
"""The status a request event names for a request whose handler raised."""


def instrument(app: FastAPI) -> None:
    """Record a request event for every request a principal makes to `app`.

    Call it before `log.instrument`, so the request id and the request line are in place around
    it.
    """
    app.add_middleware(RecordsRequests)


async def record(request: Request, status: int) -> None:
    """Record the request event for `request`, answered with `status`, when a principal made it."""
    principal: Principal | None = getattr(request.state, SERVED, None)
    if principal is None:
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
        request = Request(scope)
        answered = False

        async def recording(message: Message) -> None:
            nonlocal answered
            if message["type"] == "http.response.start":
                answered = True
                await record(request, message["status"])
            await send(message)

        try:
            await self._app(scope, receive, recording)
        finally:
            if not answered and not _cancelled():
                await record(request, FAILED)


def _cancelled() -> bool:
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0
