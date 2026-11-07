from collections.abc import AsyncIterator
from typing import final

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from app import response_limits


def finite_router() -> APIRouter:
    router = APIRouter()

    @router.get("/body/{size}")
    async def body(size: int) -> Response:
        return Response(b"x" * size)

    @router.get("/larger/{size}")
    async def larger(request: Request, size: int) -> Response:
        response_limits.allow(request, max_bytes=2048, reason="An export holds more text")
        return Response(b"y" * size)

    return router


@final
class Chunked:
    def __init__(self, chunks: list[bytes], *, declared: bytes | None = None) -> None:
        self.chunks = chunks
        self.declared = declared
        self.cleaned = False
        self.produced = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = [] if self.declared is None else [(b"content-length", self.declared)]
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        try:
            for chunk in self.chunks:
                self.produced += 1
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b""})
        finally:
            self.cleaned = True


def stream_router(*, declared: bool) -> APIRouter:
    router = APIRouter()

    @router.get("/stream")
    async def stream(request: Request) -> StreamingResponse:
        if declared:
            response_limits.stream(request, reason="The export sends bounded chunks")

        async def chunks() -> AsyncIterator[bytes]:
            yield b"a" * 700
            yield b"b" * 700

        return StreamingResponse(chunks())

    return router


def unfinished_router() -> APIRouter:
    import asyncio

    from app.audit_events import serve
    from app.identity.shared import SENTINEL_PRINCIPAL

    router = APIRouter()

    @router.get("/unfinished")
    async def unfinished(request: Request) -> StreamingResponse:
        serve(request, SENTINEL_PRINCIPAL)

        async def chunks() -> AsyncIterator[bytes]:
            yield b"first"
            await asyncio.Event().wait()

        return StreamingResponse(chunks())

    return router
