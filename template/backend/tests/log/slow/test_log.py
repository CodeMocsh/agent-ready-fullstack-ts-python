import asyncio

import httpx
import uvicorn
from fastapi import FastAPI

from tests.conftest import Logged
from tests.log.test_log import completed


async def test_uvicorn_writes_a_request_that_raised_as_one_line_with_its_request_id(
    app: FastAPI, logged: Logged
) -> None:
    """The line uvicorn writes itself when a request raises, under the server that writes it.

    `TestClient` never produces this line, so a real server runs here, on a free port, with its
    own logging configuration left out -- which is what `create_app` replaces anyway.
    """

    @app.get("/raises")
    async def raises() -> None:
        raise RuntimeError("the handler failed")

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None))
    serving = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    async with httpx.AsyncClient() as client:
        answered = await client.get(f"http://127.0.0.1:{port}/raises")
    server.should_exit = True
    await serving

    lines = logged()
    [raised] = [one for one in lines if one["message"].startswith("Exception in ASGI application")]
    [line] = completed(lines)
    assert answered.status_code == 500
    assert raised["severity"] == "ERROR"
    assert "RuntimeError: the handler failed" in raised["exception"]
    assert raised["request_id"] == line["request_id"]
