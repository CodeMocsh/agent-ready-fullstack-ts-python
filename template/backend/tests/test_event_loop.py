"""A step that holds an event loop is reported from either event loop a test runs on, and fails
the test only past `HELD_SECONDS`."""

import asyncio
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.event_loop import HELD_SECONDS, Reported


def served_holding(seconds: float) -> None:
    app = FastAPI()

    @app.get("/held")
    async def held() -> None:
        time.sleep(seconds)

    with TestClient(app) as client:
        client.get("/held")


def test_a_route_that_holds_the_event_loop_past_the_limit_fails(asyncio_reports: Reported) -> None:
    served_holding(HELD_SECONDS + 0.05)

    assert len(asyncio_reports.failures()) == 1
    asyncio_reports.held_on_purpose()


def test_a_route_that_holds_the_event_loop_briefly_is_reported_and_passes(
    asyncio_reports: Reported,
) -> None:
    served_holding(0.2)

    assert asyncio_reports.records != []
    assert asyncio_reports.failures() == []


async def test_a_test_that_holds_its_event_loop_past_the_limit_fails(
    asyncio_reports: Reported,
) -> None:
    time.sleep(HELD_SECONDS + 0.05)
    await asyncio.sleep(0)

    assert len(asyncio_reports.failures()) == 1
    asyncio_reports.held_on_purpose()
