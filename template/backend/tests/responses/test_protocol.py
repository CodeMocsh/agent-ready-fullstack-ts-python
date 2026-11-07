import asyncio
from pathlib import Path

import pytest
from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from app import response_bound, response_limits
from app.store import Database
from tests.responses.doubles import Chunked
from tests.routed import assembled


def test_a_producer_that_swallows_overflow_still_gets_a_refusal(database: Database) -> None:
    async def swallows(scope: Scope, receive: Receive, send: Send) -> None:
        producer = Chunked([b"x" * (response_limits.DEFAULT_BYTES + 1)])
        with pytest.raises(BaseException, match="Response exceeds"):
            await producer(scope, receive, send)

    app = assembled(database=database)
    app.mount("/swallows", swallows)

    assert TestClient(app).get("/swallows/").json() == {"detail": "Response exceeds its byte limit"}


def test_a_finite_response_that_never_finishes_fails_within_the_request_budget(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(response_bound, "REQUEST_BUDGET_SECONDS", 0.05)
    router = APIRouter()

    @router.get("/never")
    async def never() -> StreamingResponse:
        async def chunks():
            yield b"first"
            await asyncio.Event().wait()

        return StreamingResponse(chunks())

    response = TestClient(assembled(router, database=database)).get("/never")

    assert response.status_code == 500
    assert response.json() == {"detail": "Response did not finish within its time limit"}


def test_a_finite_response_that_returns_without_finishing_fails(database: Database) -> None:
    async def incomplete(_scope: Scope, _receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"first", "more_body": True})

    app = assembled(database=database)
    app.mount("/incomplete", incomplete)

    with pytest.raises(response_bound.ResponsePolicyError, match="final body"):
        TestClient(app).get("/incomplete/")


async def test_file_extensions_fall_back_to_counted_body_messages(tmp_path: Path) -> None:
    path = tmp_path / "file.txt"
    path.write_bytes(b"x" * 1025)
    app = response_bound.BoundsResponses(
        FileResponse(path), limits=response_limits.Limits(1024, 2048)
    )
    scope: Scope = {
        "type": "http",
        "asgi": {"spec_version": "2.4"},
        "method": "GET",
        "path": "/file",
        "headers": [],
        "extensions": {"http.response.pathsend": {}, "http.response.zerocopysend": {}},
    }
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b""}

    async def send(message: Message) -> None:
        sent.append(message)

    await app(scope, receive, send)

    assert sent[0]["status"] == 500
    assert [message["type"] for message in sent] == ["http.response.start", "http.response.body"]


def test_a_route_can_narrow_the_default(database: Database) -> None:
    router = APIRouter()

    @router.get("/small")
    async def small(request: Request) -> Response:
        response_limits.allow(request, max_bytes=1024, reason="A small representation")
        return Response(b"x" * 1025)

    assert TestClient(assembled(router, database=database)).get("/small").status_code == 500


async def test_a_disconnected_stream_releases_its_producer_without_a_second_response() -> None:
    disconnected = asyncio.Event()
    cleaned = asyncio.Event()
    sent: list[Message] = []

    async def chunks():
        try:
            yield b"first"
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    async def producer(scope: Scope, receive: Receive, send: Send) -> None:
        response_limits.stream(Request(scope), reason="The test disconnects the stream")
        await StreamingResponse(chunks())(scope, receive, send)

    async def receive() -> Message:
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)
        if message["type"] == "http.response.body":
            disconnected.set()

    app = response_bound.BoundsResponses(producer, limits=response_limits.Limits())
    async with asyncio.timeout(1):
        await app({"type": "http", "method": "GET"}, receive, send)

    assert cleaned.is_set()
    assert [message["status"] for message in sent if message["type"] == "http.response.start"] == [
        200
    ]


def test_a_transformation_outside_a_nested_guard_cannot_bypass_the_host_limit(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RESPONSE_MAX_BYTES", "1024")
    child = assembled(database=database)
    child.mount("/small", Chunked([b"small"]))

    async def enlarges(scope: Scope, receive: Receive, send: Send) -> None:
        async def transformed(message: Message) -> None:
            if message["type"] == "http.response.body":
                message = {**message, "body": b"x" * 1025}
            await send(message)

        await child(scope, receive, transformed)

    parent = assembled(database=database)
    parent.mount("/child", enlarges)
    response = TestClient(parent).get("/child/small/")

    assert response.status_code == 500
    assert response.json() == {"detail": "Response exceeds its byte limit"}


@pytest.mark.parametrize("status", [204, 304])
def test_bodyless_statuses_remain_bodyless(database: Database, status: int) -> None:
    router = APIRouter()

    @router.get("/empty")
    async def empty() -> Response:
        return Response(status_code=status)

    response = TestClient(assembled(router, database=database)).get("/empty")

    assert response.status_code == status
    assert response.content == b""
    assert "content-length" not in response.headers


def test_compression_outside_the_guard_does_not_hide_overflow(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from starlette.middleware.gzip import GZipMiddleware

    monkeypatch.setenv("RESPONSE_MAX_BYTES", "1024")
    app = assembled(database=database)
    app.mount("/large", Chunked([b"x" * 2048]))
    app.add_middleware(GZipMiddleware, minimum_size=1)

    response = TestClient(app).get("/large/")

    assert response.status_code == 500
    assert response.json() == {"detail": "Response exceeds its byte limit"}
    assert response.headers["content-encoding"] == "gzip"


def test_a_runtime_error_handler_cannot_intercept_overflow(database: Database) -> None:
    app = assembled(database=database)
    app.mount("/large", Chunked([b"x" * (response_limits.DEFAULT_BYTES + 1)]))

    async def handles(_request: Request, _error: Exception) -> Response:
        return Response("wrong handler", status_code=418)

    app.add_exception_handler(RuntimeError, handles)

    response = TestClient(app).get("/large/")

    assert response.status_code == 500
    assert response.json() == {"detail": "Response exceeds its byte limit"}


def test_a_task_group_does_not_hide_overflow(database: Database) -> None:
    async def grouped(scope: Scope, receive: Receive, send: Send) -> None:
        async with asyncio.TaskGroup() as group:
            group.create_task(
                Chunked([b"x" * (response_limits.DEFAULT_BYTES + 1)])(scope, receive, send)
            )

    app = assembled(database=database)
    app.mount("/grouped", grouped)

    response = TestClient(app).get("/grouped/")

    assert response.status_code == 500
    assert response.json() == {"detail": "Response exceeds its byte limit"}


def test_unhandled_failures_keep_the_declared_error_shape(database: Database) -> None:
    router = APIRouter()

    @router.get("/fails")
    async def fails() -> Response:
        raise ValueError("Internal details")

    response = TestClient(assembled(router, database=database), raise_server_exceptions=False).get(
        "/fails"
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}


def test_the_buffer_deadline_does_not_cancel_handler_work(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.clock import elapse

    monkeypatch.setattr(response_bound, "REQUEST_BUDGET_SECONDS", 0.01)
    router = APIRouter()

    @router.get("/delayed")
    async def delayed() -> Response:
        await elapse(0.05)
        return Response(b"finished")

    response = TestClient(assembled(router, database=database)).get("/delayed")

    assert response.status_code == 200
    assert response.content == b"finished"
