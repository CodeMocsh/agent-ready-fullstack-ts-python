"""A step that uses more than `HELD_SECONDS` of CPU on an event loop is an error at teardown of
its test, from either event loop a test runs on. A step that only waits is not."""

import asyncio

import pytest

from tests.event_loop import HELD_SECONDS, Reported, hold_the_event_loop

A_ROUTE = """
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.event_loop import hold_the_event_loop


def test_a_route() -> None:
    app = FastAPI()

    @app.get("/step")
    async def step() -> None:
        {step}

    TestClient(app).get("/step")
"""


def run_a_route(pytester: pytest.Pytester, step: str) -> pytest.RunResult:
    pytester.makeconftest("from tests.conftest import asyncio_logged, asyncio_reports\n")
    pytester.makepyfile(A_ROUTE.format(step=step))
    return pytester.runpytest("-p", "no:asyncio")


def test_a_route_that_uses_the_cpu_past_the_limit_is_an_error(
    pytester: pytest.Pytester, asyncio_reports: Reported
) -> None:
    ran = run_a_route(pytester, f"hold_the_event_loop({HELD_SECONDS + 0.05})")

    ran.assert_outcomes(passed=1, errors=1)
    assert asyncio_reports.held_on_purpose() != []


def test_a_route_that_waits_past_the_limit_without_the_cpu_passes(
    pytester: pytest.Pytester, asyncio_reports: Reported
) -> None:
    """The step a busy machine makes of a short one: long in wall time, short in CPU time."""
    ran = run_a_route(pytester, f"time.sleep({HELD_SECONDS + 0.05})")

    ran.assert_outcomes(passed=1)
    assert asyncio_reports.held_on_purpose() != []


async def test_a_test_that_uses_the_cpu_past_the_limit_on_its_event_loop_is_reported(
    asyncio_reports: Reported,
) -> None:
    hold_the_event_loop(HELD_SECONDS + 0.05)
    await asyncio.sleep(0)

    assert len(asyncio_reports.failures()) == 1
    asyncio_reports.held_on_purpose()
