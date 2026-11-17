import asyncio

import pytest
from fastapi.testclient import TestClient

from app.deployment import (
    READS_MAX_PARTS_ENV,
    READS_PART_BOUND_SECONDS_ENV,
    READS_PART_CONCURRENCY_ENV,
)
from app.main import ROUTERS
from app.store import Database
from tests.conftest import Logged
from tests.reads.test_reads import WIDGETS, parts_of, slow_router
from tests.routed import assembled


async def test_a_part_past_its_bound_leaves_its_group_and_the_next_part_answers(
    database: Database, monkeypatch: pytest.MonkeyPatch, logged: Logged
) -> None:
    monkeypatch.setenv(READS_PART_BOUND_SECONDS_ENV, "0.5")
    app = await asyncio.to_thread(assembled, *ROUTERS, WIDGETS, slow_router(5.0), database=database)

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
    count = 12
    bound_seconds = 0.8
    monkeypatch.setenv(READS_PART_BOUND_SECONDS_ENV, str(bound_seconds))
    monkeypatch.setenv(READS_MAX_PARTS_ENV, str(count))
    monkeypatch.setenv(READS_PART_CONCURRENCY_ENV, "1")
    app = await asyncio.to_thread(
        assembled,
        *ROUTERS,
        WIDGETS,
        slow_router(bound_seconds / (count - 1)),
        database=database,
    )

    parts = await asyncio.to_thread(parts_of, TestClient(app), *["/slow"] * count)

    assert [part["kind"] for part in parts] == ["answered"] * count
