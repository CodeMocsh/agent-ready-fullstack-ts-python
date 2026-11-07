import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app import response_bound, response_limits, serve
from app.pacing import yield_now
from app.store import Database
from tests.clock import elapse
from tests.responses.doubles import finite_router
from tests.routed import assembled


def open_stream(go: asyncio.Event) -> APIRouter:
    router = APIRouter()

    @router.get("/stream")
    async def stream(request: Request) -> StreamingResponse:
        response_limits.stream(request, reason="The test controls the stream lifetime")

        async def chunks() -> AsyncIterator[bytes]:
            yield b"first"
            await go.wait()
            yield b"last"

        return StreamingResponse(chunks())

    return router


async def exercise_http(base: str, go: asyncio.Event) -> None:
    async with httpx.AsyncClient(trust_env=False) as client:
        assert (await client.get(f"{base}/larger/2048")).content == b"y" * 2048
        assert (await client.get(f"{base}/body/1025")).status_code == 500
        async with client.stream("GET", f"{base}/stream") as response:
            chunks = response.aiter_bytes()
            assert await anext(chunks) == b"first"
            await elapse(1.1)
            go.set()
            assert await anext(chunks) == b"last"
            with pytest.raises(StopAsyncIteration):
                await anext(chunks)


@pytest.mark.parametrize("one_origin", [False, True])
async def test_limits_exceptions_and_incremental_delivery_over_http(
    database: Database, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, one_origin: bool
) -> None:
    monkeypatch.setenv("RESPONSE_MAX_BYTES", "1024")
    monkeypatch.setattr(response_bound, "REQUEST_BUDGET_SECONDS", 1.0)
    go = asyncio.Event()
    api = await asyncio.to_thread(assembled, finite_router(), open_stream(go), database=database)
    (tmp_path / "index.html").write_text("shell")
    app = serve.create_server(tmp_path, api) if one_origin else api
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_config=None)
    )
    async with asyncio.timeout(5), asyncio.TaskGroup() as group:
        group.create_task(server.serve())
        try:
            while not server.started:
                await yield_now()
            port = server.servers[0].sockets[0].getsockname()[1]
            prefix = "/api" if one_origin else ""
            await exercise_http(f"http://127.0.0.1:{port}{prefix}", go)
        finally:
            go.set()
            server.should_exit = True
