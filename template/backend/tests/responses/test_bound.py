from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app import response_limits, serve
from app.main import ROUTERS
from app.refusal import ResponseTooLarge
from app.store import Database
from app.wiring import build_response_limits
from tests.conftest import Logged
from tests.responses.doubles import Chunked, finite_router, stream_router
from tests.routed import assembled


@pytest.fixture
def _limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESPONSE_MAX_BYTES", "1024")
    monkeypatch.setenv("RESPONSE_CEILING_BYTES", "4096")


@pytest.mark.parametrize("size,status", [(1023, 200), (1024, 200), (1025, 500)])
def test_the_default_limit_counts_bytes(
    database: Database, _limits: None, size: int, status: int
) -> None:
    response = TestClient(assembled(finite_router(), database=database)).get(f"/body/{size}")

    assert response.status_code == status
    if status == 200:
        assert response.content == b"x" * size
    else:
        assert response.json() == {"detail": ResponseTooLarge.description}
        assert response.headers["cache-control"] == "no-store"
        assert len(response.content) < 1024


def test_a_route_exception_does_not_change_other_requests(
    database: Database, _limits: None
) -> None:
    client = TestClient(assembled(finite_router(), database=database))

    assert client.get("/larger/2048").content == b"y" * 2048
    assert client.get("/larger/2049").status_code == 500
    assert client.get("/body/2048").status_code == 500


@pytest.mark.parametrize("maximum", [0, 1023, 4097, True, 2048.5])
def test_a_route_cannot_escape_the_deployment_ceiling(
    database: Database, _limits: None, maximum: int
) -> None:
    router = APIRouter()

    @router.get("/invalid")
    async def invalid(request: Request) -> Response:
        response_limits.allow(request, max_bytes=maximum, reason="An export")
        return Response(b"small")

    with pytest.raises(response_limits.ResponsePolicyInvalid):
        TestClient(assembled(router, database=database)).get("/invalid")


@pytest.mark.parametrize("value", ["0", "-1", "one", "1.5", "4097"])
def test_invalid_deployment_limits_fail_before_serving(
    monkeypatch: pytest.MonkeyPatch, _limits: None, value: str
) -> None:
    monkeypatch.setenv("RESPONSE_MAX_BYTES", value)

    with pytest.raises(response_limits.ResponsePolicyInvalid):
        build_response_limits()


def test_bytes_include_multibyte_text(database: Database, _limits: None) -> None:
    router = APIRouter()

    @router.get("/text")
    async def text() -> Response:
        return Response("界" * 400)

    assert TestClient(assembled(router, database=database)).get("/text").status_code == 500


@pytest.mark.parametrize("declared", [None, b"1", b"2048"])
def test_a_mounted_producer_cannot_bypass_the_limit_with_headers(
    database: Database, _limits: None, declared: bytes | None
) -> None:
    producer = Chunked([b"a" * 700, b"b" * 700, b"never"], declared=declared)
    app = assembled(database=database)
    app.mount("/mounted", producer)

    response = TestClient(app).get("/mounted/")

    assert response.status_code == 500
    assert response.json() == {"detail": ResponseTooLarge.description}
    assert producer.cleaned
    assert producer.produced == 2
    assert int(response.headers["content-length"]) == len(response.content)


def test_nested_apps_share_the_outer_policy(database: Database, _limits: None) -> None:
    parent = assembled(database=database)
    parent.mount("/mounted", assembled(finite_router(), database=database))
    client = TestClient(parent)

    assert client.get("/mounted/larger/2048").content == b"y" * 2048
    assert client.get("/mounted/body/1025").status_code == 500


def test_one_origin_keeps_the_route_exception_and_bounds_assets(
    database: Database, _limits: None, tmp_path: Path
) -> None:
    (tmp_path / "index.html").write_text("shell")
    (tmp_path / "large.txt").write_bytes(b"x" * 4097)
    (tmp_path / "medium.txt").write_bytes(b"x" * 2048)
    app = serve.create_server(tmp_path, assembled(finite_router(), database=database))
    client = TestClient(app)

    assert client.get("/api/larger/2048").content == b"y" * 2048
    assert client.get("/large.txt").status_code == 500
    assert client.get("/medium.txt").content == b"x" * 2048
    assert client.get("/").text == "shell"


def test_a_refusal_log_names_the_cause_without_the_body(
    database: Database, _limits: None, logged: Logged
) -> None:
    app = assembled(finite_router(), database=database)
    logged()

    response = TestClient(app).get("/body/1025")
    lines = logged()
    refusal = next(line for line in lines if line["message"] == "response too large")
    completed = next(line for line in lines if line["message"] == "request completed")

    assert refusal["refusal"] == "ResponseTooLarge"
    assert (refusal["route"], refusal["limit"], refusal["observed"]) == ("/body/{size}", 1024, 1025)
    assert completed["http.response.status_code"] == response.status_code == 500
    assert refusal["request_id"] == response.headers["x-request-id"]


def test_the_contract_declares_the_refusal(database: Database) -> None:
    schema = assembled(*ROUTERS, database=database).openapi()

    assert all(
        "500" in operation["responses"]
        for path in schema["paths"].values()
        for operation in path.values()
    )


@pytest.mark.parametrize("declared,status", [(False, 500), (True, 200)])
def test_only_a_declared_stream_can_exceed_the_total_limit(
    database: Database, _limits: None, declared: bool, status: int
) -> None:
    response = TestClient(assembled(stream_router(declared=declared), database=database)).get(
        "/stream"
    )

    assert response.status_code == status
    if declared:
        assert response.content == b"a" * 700 + b"b" * 700
    else:
        assert response.json() == {"detail": ResponseTooLarge.description}


@pytest.mark.parametrize("reason", ["", "   "])
def test_an_exception_requires_a_reason(database: Database, _limits: None, reason: str) -> None:
    router = APIRouter()

    @router.get("/unnamed")
    async def unnamed(request: Request) -> Response:
        response_limits.allow(request, max_bytes=2048, reason=reason)
        return Response(b"small")

    with pytest.raises(response_limits.ResponsePolicyInvalid, match="reason"):
        TestClient(assembled(router, database=database)).get("/unnamed")


def test_a_policy_cannot_change_after_response_start(database: Database, _limits: None) -> None:
    router = APIRouter()

    @router.get("/late")
    async def late(request: Request) -> StreamingResponse:
        async def chunks() -> AsyncIterator[bytes]:
            response_limits.stream(request, reason="Too late")
            yield b"small"

        return StreamingResponse(chunks())

    with pytest.raises(response_limits.ResponsePolicyInvalid, match="precede"):
        TestClient(assembled(router, database=database)).get("/late")


def test_one_origin_also_bounds_an_api_without_its_own_guard(_limits: None, tmp_path: Path) -> None:
    from fastapi import FastAPI

    (tmp_path / "index.html").write_text("shell")
    api = FastAPI()
    api.mount("/raw", Chunked([b"x" * 1025]))
    client = TestClient(serve.create_server(tmp_path, api))

    assert client.get("/api/raw/").status_code == 500


def test_declaring_a_stream_does_not_exempt_a_failure(database: Database, _limits: None) -> None:
    router = APIRouter()

    @router.get("/stream-error")
    async def stream_error(request: Request) -> Response:
        response_limits.stream(request, reason="An export")
        return Response(b"x" * 1025, status_code=400)

    response = TestClient(assembled(router, database=database)).get("/stream-error")

    assert response.status_code == 500
    assert response.json() == {"detail": ResponseTooLarge.description}


def test_a_stream_does_not_advertise_a_buffered_length(database: Database, _limits: None) -> None:
    router = APIRouter()

    @router.get("/download")
    async def download(request: Request) -> Response:
        response_limits.stream(request, reason="A download")
        return Response(b"x" * 2048)

    response = TestClient(assembled(router, database=database)).get("/download")

    assert response.content == b"x" * 2048
    assert "content-length" not in response.headers


@pytest.mark.parametrize(
    "default,ceiling", [(True, 4096), (1024.5, 4096), (1024, 4096.5), (1024, 0)]
)
def test_direct_policy_values_must_be_integer_byte_counts(default: int, ceiling: int) -> None:
    with pytest.raises(response_limits.ResponsePolicyInvalid):
        response_limits.Limits(default=default, ceiling=ceiling)


@pytest.mark.parametrize("value", ["0", "-1", "text", "1.5", "1023"])
def test_invalid_ceilings_fail_before_serving(
    monkeypatch: pytest.MonkeyPatch, _limits: None, value: str
) -> None:
    monkeypatch.setenv("RESPONSE_CEILING_BYTES", value)
    with pytest.raises(response_limits.ResponsePolicyInvalid):
        build_response_limits()


def test_a_chunked_response_gets_its_measured_length(database: Database, _limits: None) -> None:
    app = assembled(database=database)
    app.mount("/chunks", Chunked([b"a" * 400, b"b" * 400], declared=b"1"))

    response = TestClient(app).get("/chunks/")

    assert response.content == b"a" * 400 + b"b" * 400
    assert response.headers["content-length"] == "800"


def test_head_keeps_the_representation_length_without_buffering_the_file(
    database: Database, _limits: None, tmp_path: Path
) -> None:
    from fastapi.responses import FileResponse

    path = tmp_path / "large.txt"
    path.write_bytes(b"x" * 1025)
    router = APIRouter()

    @router.head("/file")
    async def file() -> FileResponse:
        return FileResponse(path)

    response = TestClient(assembled(router, database=database)).head("/file")

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-length"] == "1025"
