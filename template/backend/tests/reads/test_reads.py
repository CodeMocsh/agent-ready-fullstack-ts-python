"""`GET /reads`: each part answers as its route answers it alone, in order, and a part that cannot
answer in its group leaves it. `docs/adr/template/0029`."""

import asyncio
from pathlib import Path
from typing import Any, final

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient

from app import serve
from app.deployment import (
    ADMISSION_BURST_ENV,
    ADMISSION_PER_SECOND_ENV,
    READS_MAX_PARTS_ENV,
    READS_PART_BOUND_SECONDS_ENV,
    READS_PART_CONCURRENCY_ENV,
    RESPONSE_CEILING_BYTES_ENV,
    RESPONSE_MAX_BYTES_ENV,
)
from app.deps import resolved_principal
from app.identity.shared import SENTINEL_TENANT, Principal, Unauthenticated
from app.main import ROUTERS
from app.reads import PART, PATHS_AT_MOST
from app.refusal import ApiError, GroupTooLarge
from app.store import Database
from app.waits import PartWaitsOutside, waiting
from tests.clock import elapse
from tests.conftest import Logged
from tests.pages import every_event
from tests.routed import assembled, routed


def parts_of(client: TestClient, *paths: str, at: str = "/reads") -> list[dict[str, Any]]:
    response = client.get(at, params=[("part", path) for path in paths])
    assert response.status_code == 200, response.text
    return response.json()["parts"]


REFUSAL = "this group carries no credential"


async def refusing() -> Principal:
    raise Unauthenticated(REFUSAL)


@final
class NoSuchWidget(ApiError):
    status = 404
    description = "Widget not found"


def widgets_router() -> APIRouter:
    router = APIRouter(dependencies=[Depends(resolved_principal)])

    @router.get("/widgets", openapi_extra=PART)
    async def widgets(size: int = 2) -> dict[str, str]:
        return {"widgets": "w" * size}

    @router.get("/widgets/{id}", openapi_extra=PART)
    async def widget(id: str) -> dict[str, str]:
        if id != "one":
            raise NoSuchWidget
        return {"widget": id}

    @router.get("/widgets/{id}/merged", openapi_extra={**PART, "x-other": True})
    async def merged(id: str) -> dict[str, str]:
        return {"merged": id}

    @router.get("/widgets/{id}/alone")
    async def alone(id: str) -> dict[str, str]:
        return {"alone": id}

    return router


WIDGETS = widgets_router()


def slow_router(seconds: float, *, wait_outside: bool = False) -> APIRouter:
    router = APIRouter()

    @router.get("/slow", openapi_extra=PART)
    async def slow() -> dict[str, str]:
        if wait_outside:
            async with waiting(seconds, on="test.outside"):
                pass
        await elapse(seconds)
        return {"slow": "answered"}

    return router


async def test_each_part_answers_in_order_as_its_route_answers_alone(database: Database) -> None:
    client = TestClient(await asyncio.to_thread(routed, *ROUTERS, WIDGETS, database=database))

    parts = await asyncio.to_thread(parts_of, client, "/widgets", "/widgets/two", "/widgets?size=5")
    alone = [
        await asyncio.to_thread(client.get, path)
        for path in ("/widgets", "/widgets/two", "/widgets?size=5")
    ]

    assert [part["kind"] for part in parts] == ["answered"] * 3
    assert [part["status"] for part in parts] == [one.status_code for one in alone]
    assert [part["body"] for part in parts] == [one.text for one in alone]
    assert parts[0]["headers"]["content-type"] == "application/json"
    assert "content-length" not in parts[0]["headers"]


async def test_a_path_that_is_no_part_leaves_its_group(database: Database) -> None:
    client = TestClient(await asyncio.to_thread(routed, *ROUTERS, WIDGETS, database=database))

    parts = await asyncio.to_thread(
        parts_of,
        client,
        "/health",
        "/reads?part=/widgets",
        "/nowhere",
        "/widgets/one/alone",
        "/widgets",
    )

    assert [part["kind"] for part in parts] == ["left", "left", "left", "left", "answered"]
    assert {part.get("reason") for part in parts[:4]} == {"not a part"}


async def test_a_route_that_merges_part_into_its_openapi_extra_is_a_part(
    database: Database,
) -> None:
    client = TestClient(await asyncio.to_thread(routed, *ROUTERS, WIDGETS, database=database))

    parts = await asyncio.to_thread(parts_of, client, "/widgets/one/merged")

    assert parts == [
        {
            "kind": "answered",
            "status": 200,
            "headers": parts[0]["headers"],
            "body": '{"merged":"one"}',
        }
    ]


async def test_each_part_is_a_request_event_with_the_request_id_of_its_group(
    database: Database,
) -> None:
    client = TestClient(await asyncio.to_thread(routed, *ROUTERS, WIDGETS, database=database))

    response = await asyncio.to_thread(
        client.get, "/reads", params=[("part", "/widgets"), ("part", "/widgets/two")]
    )
    events = await every_event(database.audit_events(SENTINEL_TENANT))

    group = response.headers["x-request-id"]
    assert sorted((event.route, event.status, event.request_id) for event in events) == [
        ("/widgets", 200, group),
        ("/widgets/{id}", 404, group),
    ]
    assert {part["headers"]["x-request-id"] for part in response.json()["parts"]} == {group}


async def test_a_part_past_its_bound_leaves_its_group_and_the_next_part_answers(
    database: Database, monkeypatch: pytest.MonkeyPatch, logged: Logged
) -> None:
    monkeypatch.setenv(READS_PART_BOUND_SECONDS_ENV, "0.05")
    app = await asyncio.to_thread(assembled, *ROUTERS, WIDGETS, slow_router(1.0), database=database)

    parts = await asyncio.to_thread(parts_of, TestClient(app), "/slow", "/widgets")

    assert parts[0] == {"kind": "left", "reason": "timed out"}
    assert (parts[1]["kind"], parts[1]["status"]) == ("answered", 200)
    lines = logged()
    left = [line for line in lines if line["message"] == "part left its group"]
    assert [(line["route"], line["reason"], line["parts"]) for line in left] == [
        ("/slow", "timed out", 1)
    ]
    slow = [line for line in lines if line.get("http.route") == "/slow"]
    assert [line["message"] for line in slow] == ["request cancelled"]


async def test_each_part_s_bound_starts_at_its_turn(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(READS_PART_BOUND_SECONDS_ENV, "0.3")
    monkeypatch.setenv(READS_PART_CONCURRENCY_ENV, "1")
    app = await asyncio.to_thread(
        assembled, *ROUTERS, WIDGETS, slow_router(0.15), database=database
    )

    parts = await asyncio.to_thread(parts_of, TestClient(app), "/slow", "/slow", "/slow")

    assert [part["kind"] for part in parts] == ["answered"] * 3


async def test_a_part_past_the_cap_leaves_its_group(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(READS_MAX_PARTS_ENV, "2")
    app = await asyncio.to_thread(assembled, *ROUTERS, WIDGETS, database=database)

    parts = await asyncio.to_thread(parts_of, TestClient(app), "/widgets", "/widgets", "/widgets")

    assert [part["kind"] for part in parts] == ["answered", "answered", "left"]
    assert parts[2]["reason"] == "past the cap"


async def test_a_group_runs_its_concurrency_of_parts_at_once(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(READS_PART_CONCURRENCY_ENV, "2")
    running = 0
    most = 0
    router = APIRouter()

    @router.get("/counted", openapi_extra=PART)
    async def counted() -> dict[str, int]:
        nonlocal running, most
        running += 1
        most = max(most, running)
        await elapse(0.02)
        running -= 1
        return {"most": most}

    app = await asyncio.to_thread(assembled, *ROUTERS, WIDGETS, router, database=database)

    parts = await asyncio.to_thread(parts_of, TestClient(app), *["/counted"] * 6)

    assert [part["status"] for part in parts] == [200] * 6
    assert most == 2


async def test_each_part_is_charged_to_the_rate_of_its_actor(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ADMISSION_BURST_ENV, "3")
    monkeypatch.setenv(ADMISSION_PER_SECOND_ENV, "0.01")
    monkeypatch.setenv(READS_MAX_PARTS_ENV, "3")
    app = await asyncio.to_thread(assembled, *ROUTERS, WIDGETS, database=database)
    client = TestClient(app)

    first = await asyncio.to_thread(parts_of, client, "/widgets", "/widgets", "/widgets")
    second = await asyncio.to_thread(parts_of, client, "/widgets")

    assert [part["status"] for part in first] == [200, 200, 200]
    assert second[0]["status"] == 429
    assert "retry-after" in second[0]["headers"]


async def test_each_part_presents_the_credential_of_its_group(database: Database) -> None:
    app = await asyncio.to_thread(assembled, *ROUTERS, WIDGETS, database=database)
    app.dependency_overrides[resolved_principal] = refusing

    parts = await asyncio.to_thread(parts_of, TestClient(app), "/widgets", "/widgets/two")

    assert [part["status"] for part in parts] == [401, 401]
    assert all(REFUSAL in part["body"] for part in parts)
    assert parts[0]["headers"]["www-authenticate"] == "Bearer"


async def test_a_part_that_waits_outside_the_database_fails_its_group(
    database: Database,
) -> None:
    app = await asyncio.to_thread(
        assembled, *ROUTERS, WIDGETS, slow_router(0.01, wait_outside=True), database=database
    )

    with pytest.raises(ExceptionGroup) as failed:
        await asyncio.to_thread(parts_of, TestClient(app), "/widgets", "/slow")

    assert failed.value.subgroup(PartWaitsOutside) is not None


async def test_a_part_whose_answer_does_not_fit_the_ceiling_leaves_its_group(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(RESPONSE_MAX_BYTES_ENV, "12288")
    monkeypatch.setenv(RESPONSE_CEILING_BYTES_ENV, "12288")
    app = await asyncio.to_thread(assembled, *ROUTERS, WIDGETS, database=database)

    parts = await asyncio.to_thread(
        parts_of, TestClient(app), "/widgets?size=7000", "/widgets?size=7000", "/widgets?size=7000"
    )

    assert [part["kind"] for part in parts] == ["answered", "left", "left"]
    assert {part["reason"] for part in parts[1:]} == {"too large"}


async def test_a_group_of_many_large_parts_stays_under_the_ceiling(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(RESPONSE_MAX_BYTES_ENV, "12288")
    monkeypatch.setenv(RESPONSE_CEILING_BYTES_ENV, "12288")
    app = await asyncio.to_thread(assembled, *ROUTERS, WIDGETS, database=database)

    parts = await asyncio.to_thread(
        parts_of, TestClient(app), "/widgets?size=9000", *["/widgets?size=7000"] * 6
    )

    assert [part["kind"] for part in parts] == ["answered"] + ["left"] * 6


async def test_a_group_whose_response_cannot_name_each_path_is_refused(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(RESPONSE_MAX_BYTES_ENV, "4096")
    monkeypatch.setenv(RESPONSE_CEILING_BYTES_ENV, "4096")
    app = await asyncio.to_thread(assembled, *ROUTERS, WIDGETS, database=database)

    response = await asyncio.to_thread(
        TestClient(app).get, "/reads", params=[("part", "/nowhere")] * 200
    )

    assert response.status_code == 422
    assert response.json() == {"detail": GroupTooLarge.description}


async def test_a_group_of_more_paths_than_it_may_name_is_refused(database: Database) -> None:
    client = TestClient(await asyncio.to_thread(routed, *ROUTERS, WIDGETS, database=database))

    response = await asyncio.to_thread(
        client.get, "/reads", params=[("part", "/widgets")] * (PATHS_AT_MOST + 1)
    )

    assert response.status_code == 422


async def test_a_group_answers_on_one_origin(database: Database, tmp_path: Path) -> None:
    app = await asyncio.to_thread(assembled, *ROUTERS, WIDGETS, database=database)
    (tmp_path / "index.html").write_text("shell")
    client = TestClient(serve.create_server(tmp_path, app))

    parts = await asyncio.to_thread(parts_of, client, "/widgets", "/widgets/two", at="/api/reads")

    assert [part["status"] for part in parts] == [200, 404]


async def test_a_group_with_no_part_is_refused(database: Database) -> None:
    client = TestClient(await asyncio.to_thread(routed, *ROUTERS, WIDGETS, database=database))

    response = await asyncio.to_thread(client.get, "/reads")

    assert response.status_code == 422
