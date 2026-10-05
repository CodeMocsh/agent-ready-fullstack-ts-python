"""The log: one JSON object per line, named so every cloud reads it, and nothing in it that a
request carried.

Driven through a real `TestClient`, because the guarantee is about what reaches stdout -- a
test that called the event functions directly would keep passing after somebody stopped
calling them, or started logging somewhere else.
"""

import asyncio
import json
import logging
from collections.abc import Iterator
from typing import Annotated, Any
from uuid import uuid4

import httpx
import pytest
import uvicorn
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient

from app import log
from app.deployment import RESOURCE_ATTRIBUTES_ENV
from app.identity import Unauthenticated
from app.main import create_app
from tests.conftest import Logged
from tests.doubles import CANARY
from tests.widgets import router as widgets_router, with_widgets

SEVERITIES = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
TENANT_HEADER = "x-test-tenant"


def tenant_from_header(request: Request) -> str:
    """A seam that resolves the tenant `TENANT_HEADER` names, and refuses a request naming none."""
    named = request.headers.get(TENANT_HEADER)
    if named is None:
        raise Unauthenticated("this request names no tenant")
    return named


def named_tenant(request: Request, tenant: Annotated[str, Depends(tenant_from_header)]) -> str:
    """The tenant `tenant_from_header` resolved, named on the request line the way the shipped
    seam names its own."""
    log.name_on_request_line(request, log.TENANT_ID, tenant)
    return tenant


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    return with_widgets(create_app())


@pytest.fixture
def client(app: FastAPI, logged: Logged) -> Iterator[TestClient]:
    """A served app whose boot lines are already read, so a test sees only its own requests."""
    with TestClient(app) as fresh:
        logged()
        yield fresh


@pytest.fixture
def guarded(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """The app, also serving the widgets and `/raises` behind `named_tenant`."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = create_app()
    failing = APIRouter()

    @failing.get("/raises")
    async def raises() -> None:
        raise RuntimeError("the handler failed")

    for router in (widgets_router, failing):
        app.include_router(router, dependencies=[Depends(named_tenant)])
    return app


def completed(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [one for one in lines if one["message"] == "request completed"]


def test_every_line_is_one_json_object_with_the_fields_every_cloud_reads(
    client: TestClient, logged: Logged
) -> None:
    client.get("/widgets")

    lines = logged()

    assert lines
    for line in lines:
        assert line["severity"] in SEVERITIES
        assert line["time"].endswith("Z")
        assert isinstance(line["message"], str)
        assert isinstance(line["logger"], str)


@pytest.mark.parametrize(
    ("said", "version"),
    [("service.version=1.4.2", "1.4.2"), ("deployment.environment.name=prod", None)],
)
def test_every_line_names_the_version_the_deployment_gave_and_none_when_it_gave_none(
    logged: Logged, monkeypatch: pytest.MonkeyPatch, said: str, version: str | None
) -> None:
    """What lets a session that spans a deploy be read: each line says which build wrote it."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv(RESOURCE_ATTRIBUTES_ENV, said)

    with TestClient(with_widgets(create_app())) as client:
        client.get("/widgets")

    lines = logged()

    assert completed(lines)
    assert {line["service.version"] for line in lines} == {version}


def test_the_boot_lines_are_in_the_same_format(app: FastAPI, logged: Logged) -> None:
    """The lines a deployment greps first -- which substrate, whether it authenticates -- are
    written before any request, so they are the ones a request-only test would miss."""
    with TestClient(app):
        pass

    said = {line["message"]: line for line in logged()}

    assert said["serving"]["substrate"] == "memory"
    assert said["serving"]["severity"] == "INFO"


def test_a_request_is_logged_once_by_its_route_template_and_the_id_it_answered_with(
    client: TestClient, logged: Logged
) -> None:
    answered = client.get("/widgets/does-not-exist")

    [line] = completed(logged())

    assert line["http.request.method"] == "GET"
    assert line["http.route"] == "/widgets/{id}"
    assert line["http.response.status_code"] == 404
    assert line["request_id"] == answered.headers["x-request-id"]
    assert line["duration_ms"] >= 0


def test_a_request_to_a_prefixed_router_is_logged_by_the_whole_template(
    client: TestClient, logged: Logged
) -> None:
    """The route a router declares is only the end of the template: the prefix it was included
    under is the rest, and a line without it names a route nobody serves."""
    client.get("/shelves/top/widgets/does-not-exist")

    [line] = completed(logged())

    assert line["http.route"] == "/shelves/{shelf}/widgets/{id}"


def test_each_request_is_logged_with_the_tenant_it_resolved(
    guarded: FastAPI, logged: Logged
) -> None:
    """What lets one tenant's requests be read apart from another's. A request that resolved a
    tenant names it whatever it answered; one that resolved none -- a public route, or the seam
    refusing -- names none."""
    with TestClient(guarded, raise_server_exceptions=False) as client:
        logged()
        client.get("/widgets", headers={TENANT_HEADER: "tenant-a"})
        client.get("/widgets", headers={TENANT_HEADER: "tenant-b"})
        client.get("/widgets/does-not-exist", headers={TENANT_HEADER: "tenant-a"})
        client.get("/raises", headers={TENANT_HEADER: "tenant-b"})
        client.get("/widgets")
        client.get("/health")

    lines = completed(logged())

    assert [
        (line["http.route"], line["http.response.status_code"], line["tenant_id"]) for line in lines
    ] == [
        ("/widgets", 200, "tenant-a"),
        ("/widgets", 200, "tenant-b"),
        ("/widgets/{id}", 404, "tenant-a"),
        ("/raises", 500, "tenant-b"),
        ("/widgets", 401, None),
        ("/health", 200, None),
    ]


def test_a_field_the_project_declares_is_on_every_request_line(
    guarded: FastAPI, logged: Logged, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project that resolves more than a tenant names it the same way, once it declares it."""
    monkeypatch.setattr(log, "PROJECT_FIELDS", frozenset({"user.id"}))

    def tenant_and_user(request: Request) -> str:
        log.name_on_request_line(request, "user.id", "user-1")
        return tenant_from_header(request)

    guarded.dependency_overrides[tenant_from_header] = tenant_and_user
    with TestClient(guarded) as client:
        logged()
        client.get("/widgets", headers={TENANT_HEADER: "tenant-a"})
        client.get("/health")

    lines = completed(logged())

    assert [(line["tenant_id"], line["user.id"]) for line in lines] == [
        ("tenant-a", "user-1"),
        (None, None),
    ]


def test_a_field_nobody_declared_is_refused_rather_than_logged(
    guarded: FastAPI, logged: Logged
) -> None:
    def naming_an_email(request: Request) -> str:
        log.name_on_request_line(request, "email", CANARY)
        return tenant_from_header(request)

    guarded.dependency_overrides[tenant_from_header] = naming_an_email
    with TestClient(guarded) as client, pytest.raises(log.UndeclaredRequestField):
        client.get("/widgets", headers={TENANT_HEADER: "tenant-a"})

    assert CANARY not in json.dumps(logged())


def test_a_request_no_route_answers_is_logged_without_a_route(
    client: TestClient, logged: Logged
) -> None:
    client.get("/no-route-answers-this")

    [line] = completed(logged())

    assert line["http.route"] is None
    assert line["http.response.status_code"] == 404


def test_a_request_id_the_client_sent_is_kept(client: TestClient, logged: Logged) -> None:
    """So an event the browser reports can name the request that failed, and the two lines
    join up."""
    sent = uuid4().hex

    answered = client.get("/widgets", headers={"x-request-id": sent})

    assert answered.headers["x-request-id"] == sent
    assert completed(logged())[0]["request_id"] == sent


def test_a_request_id_that_is_not_one_is_replaced(client: TestClient, logged: Logged) -> None:
    answered = client.get("/widgets", headers={"x-request-id": CANARY})

    assert answered.headers["x-request-id"] != CANARY
    assert CANARY not in json.dumps(logged())


def test_nothing_the_request_carried_reaches_the_log(client: TestClient, logged: Logged) -> None:
    """The body, the query string, a header, a path parameter and a refused body -- each the
    way a real leak has happened. The count is asserted as well, because an empty log passes
    the absence check just as happily as a clean one."""
    client.post(f"/widgets?note={CANARY}", json={"name": CANARY}, headers={"x-note": CANARY})
    client.get(f"/widgets/{CANARY}")
    client.post("/widgets", json={"name": {"nested": CANARY}})
    client.get(f"/no-route/{CANARY}")

    lines = logged()

    assert len(completed(lines)) == 4
    assert CANARY not in json.dumps(lines)


def test_an_exception_a_request_raised_is_logged_as_a_500(app: FastAPI, logged: Logged) -> None:
    @app.get("/raises")
    async def raises() -> None:
        raise RuntimeError("the handler failed")

    with TestClient(app, raise_server_exceptions=False) as client:
        logged()
        client.get("/raises")

    [line] = completed(logged())

    assert line["http.response.status_code"] == 500
    assert line["http.route"] == "/raises"


@pytest.mark.usefixtures("client")
def test_a_library_record_carries_its_traceback_in_one_field(logged: Logged) -> None:
    """What uvicorn writes when a request raises: a foreign record, from a logger this project
    does not own. A multi-line traceback breaks every log agent that reads stdout, and
    `exception` is the field GCP's Error Reporting groups on."""
    try:
        raise RuntimeError("the handler failed")
    except RuntimeError:
        logging.getLogger("uvicorn.error").exception("Exception in ASGI application")

    [line] = logged()

    assert line["severity"] == "ERROR"
    assert line["logger"] == "uvicorn.error"
    assert line["exception"].startswith("Traceback")
    assert "RuntimeError: the handler failed" in line["exception"]


@pytest.mark.usefixtures("client")
def test_uvicorn_access_records_never_reach_the_log(logged: Logged) -> None:
    """The record uvicorn builds carries the path with its query string. `request completed`
    replaces it."""
    logging.getLogger("uvicorn.access").info(
        '%s - "GET /widgets?note=%s HTTP/1.1" 200', "::1", CANARY
    )

    assert logged() == []


@pytest.mark.usefixtures("client")
def test_a_library_says_nothing_below_a_warning(logged: Logged) -> None:
    """An HTTP client logs every URL it requests at `INFO`, and a URL carries whatever its query
    string does. Below `WARNING` the log is this project's own lines and uvicorn's lifecycle."""
    logging.getLogger("httpx").info("HTTP Request: GET https://example.com/?email=%s", CANARY)
    logging.getLogger("httpx").warning("a library warning")

    assert [line["message"] for line in logged()] == ["a library warning"]


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
