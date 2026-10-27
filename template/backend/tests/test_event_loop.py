"""A step that uses more than `HELD_SECONDS` of CPU on the reference core, on an event loop, is
an error at teardown of its test, from either event loop a test runs on. A step that only waits
is not. A garbage collection that runs during a step does not count. The limit on this machine
is `HELD_SECONDS` scaled by how fast it runs a fixed workload."""

import asyncio

import pytest

from tests.event_loop import (
    HELD_SECONDS,
    REFERENCE_WORKLOAD_SECONDS,
    Reported,
    StepLimit,
    hold_the_event_loop,
)

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


A_ROUTE_THAT_COLLECTS = """
import gc
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_a_route_that_collects() -> None:
    app = FastAPI()

    @app.get("/step")
    async def step() -> float:
        collected = 0.0
        while collected < {seconds}:
            started = time.thread_time()
            gc.collect()
            collected += time.thread_time() - started
        return collected

    assert TestClient(app).get("/step").json() >= {seconds}
"""


def run(pytester: pytest.Pytester, source: str) -> pytest.RunResult:
    pytester.makeconftest("from tests.conftest import asyncio_logged, asyncio_reports\n")
    pytester.makepyfile(source)
    return pytester.runpytest("-p", "no:asyncio")


def run_a_route(pytester: pytest.Pytester, step: str) -> pytest.RunResult:
    return run(pytester, A_ROUTE.format(step=step))


def test_a_route_that_uses_the_cpu_past_the_limit_is_an_error_that_names_the_reference_core(
    pytester: pytest.Pytester, asyncio_reports: Reported, step_limit: StepLimit
) -> None:
    ran = run_a_route(pytester, f"hold_the_event_loop({step_limit.seconds + 0.05})")

    ran.assert_outcomes(passed=1, errors=1)
    ran.stdout.fnmatch_lines(["*seconds of CPU, which is * seconds on the reference core*"])
    assert asyncio_reports.held_on_purpose() != []


def test_a_route_that_uses_the_cpu_under_the_limit_passes(
    pytester: pytest.Pytester, asyncio_reports: Reported, step_limit: StepLimit
) -> None:
    ran = run_a_route(pytester, f"hold_the_event_loop({step_limit.seconds / 2})")

    ran.assert_outcomes(passed=1)
    asyncio_reports.held_on_purpose()


def test_a_route_that_waits_past_the_limit_without_the_cpu_passes(
    pytester: pytest.Pytester, asyncio_reports: Reported, step_limit: StepLimit
) -> None:
    """The step a busy machine makes of a short one: long in wall time, short in CPU time."""
    ran = run_a_route(pytester, f"time.sleep({step_limit.seconds + 0.05})")

    ran.assert_outcomes(passed=1)
    assert asyncio_reports.held_on_purpose() != []


def test_a_route_whose_garbage_collections_use_the_cpu_past_the_limit_passes(
    pytester: pytest.Pytester, asyncio_reports: Reported, step_limit: StepLimit
) -> None:
    ran = run(pytester, A_ROUTE_THAT_COLLECTS.format(seconds=2 * step_limit.seconds))

    ran.assert_outcomes(passed=1)
    assert asyncio_reports.held_on_purpose() != []


async def test_a_test_that_uses_the_cpu_past_the_limit_on_its_event_loop_is_reported(
    asyncio_reports: Reported, step_limit: StepLimit
) -> None:
    hold_the_event_loop(step_limit.seconds + 0.05)
    await asyncio.sleep(0)

    assert len(asyncio_reports.failures()) == 1
    asyncio_reports.held_on_purpose()


def test_the_limit_on_the_reference_core_is_held_seconds() -> None:
    assert StepLimit(REFERENCE_WORKLOAD_SECONDS).seconds == HELD_SECONDS


def test_the_limit_on_a_faster_machine_comes_to_held_seconds_on_the_reference_core() -> None:
    faster = StepLimit(REFERENCE_WORKLOAD_SECONDS / 4)

    assert faster.seconds == pytest.approx(HELD_SECONDS / 4)
    assert faster.on_the_reference_core(faster.seconds) == pytest.approx(HELD_SECONDS)
