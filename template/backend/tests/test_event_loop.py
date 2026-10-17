"""A step that holds an event loop past `HELD_SECONDS` is an error at teardown of its test, from
either event loop a test runs on."""

import asyncio
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.event_loop import HELD_SECONDS, Reported

HOLDING_ROUTE = f"""
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_holding() -> None:
    app = FastAPI()

    @app.get("/held")
    async def held() -> None:
        time.sleep({HELD_SECONDS + 0.05})

    TestClient(app).get("/held")
"""


def test_a_route_that_holds_the_event_loop_past_the_limit_is_an_error(
    pytester: pytest.Pytester, asyncio_reports: Reported
) -> None:
    pytester.makeconftest("from tests.conftest import asyncio_logged, asyncio_reports\n")
    pytester.makepyfile(HOLDING_ROUTE)

    ran = pytester.runpytest("-p", "no:asyncio")

    ran.assert_outcomes(passed=1, errors=1)
    assert asyncio_reports.held_on_purpose() != []


def test_a_route_that_holds_the_event_loop_briefly_is_reported_and_passes(
    asyncio_reports: Reported,
) -> None:
    app = FastAPI()

    @app.get("/held")
    async def held() -> None:
        time.sleep(0.2)

    TestClient(app).get("/held")

    assert asyncio_reports.records != []
    assert asyncio_reports.failures() == []


async def test_a_test_that_holds_its_event_loop_past_the_limit_is_reported(
    asyncio_reports: Reported,
) -> None:
    time.sleep(HELD_SECONDS + 0.05)
    await asyncio.sleep(0)

    assert len(asyncio_reports.failures()) == 1
    asyncio_reports.held_on_purpose()
