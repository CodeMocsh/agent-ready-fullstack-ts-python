"""Hold each finite response until its bytes fit the request's declared limit."""

import asyncio
from typing import final

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import log, response_limits
from app.models import ErrorBody
from app.refusal import ResponseTimedOut, ResponseTooLarge
from app.waits import REQUEST_BUDGET_SECONDS


class _Overflow(BaseException):
    """The producer emitted more bytes than one guard permits."""

    def __init__(self, guard: object) -> None:
        super().__init__("Response exceeds its byte limit")
        self.guard: object = guard


def instrument(app: FastAPI, limits: response_limits.Limits) -> None:
    """Install before request events, admission, logging and telemetry."""
    app.add_middleware(BoundsResponses, limits=limits)


@final
class BoundsResponses:
    def __init__(
        self,
        app: ASGIApp,
        *,
        limits: response_limits.Limits,
    ) -> None:
        self._app = app
        self._limits = limits

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        scope["extensions"] = {}
        deadline = asyncio.timeout(None)
        held = _Held(scope, receive, send, self._limits, deadline)
        try:
            async with deadline:
                await self._respond(scope, held)
        except TimeoutError:
            if not deadline.expired():
                raise
            await held.timed_out(receive)

    async def _respond(self, scope: Scope, held: "_Held") -> None:
        try:
            await self._app(scope, held.receive, held.send)
        except* _Overflow as overflow:
            foreign = overflow.subgroup(
                lambda error: isinstance(error, _Overflow) and error.guard is not held
            )
            if foreign is not None:
                raise foreign from None
            held.overflow = True
        if held.overflow:
            held.deadline.reschedule(None)
            await held.refuse(held.receive)
        elif not held.complete and not held.disconnected:
            raise ResponsePolicyError("A response ended without its final body")


@final
class _Held:
    def __init__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        limits: response_limits.Limits,
        deadline: asyncio.Timeout,
    ) -> None:
        self.read = receive
        self.disconnected = False
        self.deadline = deadline
        self.complete = False
        self.scope = scope
        self.forward = send
        self.declaration = response_limits.declare(scope, limits)
        self.start: Message | None = None
        self.body = bytearray()
        self.observed = 0
        self.overflow = False

    async def receive(self) -> Message:
        message = await self.read()
        if message["type"] == "http.disconnect":
            self.disconnected = True
        return message

    async def send(self, message: Message) -> None:
        if self.overflow:
            raise _Overflow(self)
        kind = message["type"]
        if kind == "http.response.start":
            await self._start(message)
        elif self.declaration.streaming:
            if kind == "http.response.body" and not message.get("more_body", False):
                self.complete = True
            await self.forward(message)
        elif kind == "http.response.body":
            await self._body(message)
        else:
            raise ResponsePolicyError(f"A finite response cannot send {kind}")

    async def _start(self, message: Message) -> None:
        self.declaration.started = True
        self.start = message
        self.declaration.streaming = self.declaration.streaming and 200 <= message["status"] < 300
        if not self.declaration.streaming:
            self.deadline.reschedule(asyncio.get_running_loop().time() + REQUEST_BUDGET_SECONDS)
        if self.declaration.streaming:
            self.deadline.reschedule(None)
            headers = [
                (name, value)
                for name, value in message.get("headers", [])
                if name.lower() not in (b"content-length", b"transfer-encoding")
            ]
            await self.forward({**message, "headers": headers})

    async def _body(self, message: Message) -> None:
        chunk: bytes = message.get("body", b"")
        self.observed += len(chunk)
        if self.observed > self.declaration.maximum:
            self.overflow = True
            self.body.clear()
            raise _Overflow(self)
        if not self.body and not message.get("more_body", False):
            await self._finish(chunk)
        else:
            self.body.extend(chunk)
            if not message.get("more_body", False):
                body = bytes(self.body)
                self.body.clear()
                await self._finish(body)

    async def _finish(self, body: bytes) -> None:
        if self.start is None:
            raise ResponsePolicyError("A response body arrived before its start")
        self.complete = True
        self.deadline.reschedule(None)
        self._length()
        await self.forward(self.start)
        await self.forward({"type": "http.response.body", "body": body})

    def _length(self) -> None:
        if self.start is None:
            raise ResponsePolicyError("A response has no start")
        if self.scope["method"] == "HEAD" or self.start["status"] in (204, 304):
            return
        headers = [
            (name, value)
            for name, value in self.start.get("headers", [])
            if name.lower() not in (b"content-length", b"transfer-encoding")
        ]
        self.start = {
            **self.start,
            "headers": [*headers, (b"content-length", str(self.observed).encode())],
        }

    async def timed_out(self, receive: Receive) -> None:
        request = Request(self.scope)
        log.response_timed_out(
            route=log.route_answering(request) if "app" in self.scope else None,
            seconds=REQUEST_BUDGET_SECONDS,
        )
        await JSONResponse(
            status_code=ResponseTimedOut.status,
            content=ErrorBody(detail=ResponseTimedOut.description).model_dump(),
            headers={"cache-control": "no-store"},
        )(self.scope, receive, self.forward)

    async def refuse(self, receive: Receive) -> None:
        request = Request(self.scope)
        log.response_too_large(
            route=log.route_answering(request) if "app" in self.scope else None,
            limit=self.declaration.maximum,
            observed=self.observed,
            reason=self.declaration.reason,
        )
        refusal = JSONResponse(
            status_code=ResponseTooLarge.status,
            content=ErrorBody(detail=ResponseTooLarge.description).model_dump(),
            headers={"cache-control": "no-store"},
        )
        await refusal(self.scope, receive, self.forward)


class ResponsePolicyError(RuntimeError):
    """A response used an ASGI message that the finite response contract does not accept."""
