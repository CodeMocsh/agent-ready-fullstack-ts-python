"""A loop held in CPU is named in the log, by where it was held. A loop held in a wait is a
delay and is named nowhere. The watch stops with its block."""

import asyncio
import difflib
import threading
import time
from typing import Any

from app import log
from app.loop_watch import REPORTED_CPU_SECONDS, TICK_SECONDS, LoopWatch
from tests.conftest import Logged
from tests.event_loop import Reported

HOLD = 2 * REPORTED_CPU_SECONDS


def holds_through_the_standard_library(seconds: float) -> None:
    until = time.thread_time() + seconds
    while time.thread_time() < until:
        difflib.SequenceMatcher(None, "a" * 200, "ab" * 100).ratio()


def waits(seconds: float) -> None:
    time.sleep(seconds)


def held(logged: Logged) -> list[dict[str, Any]]:
    return [line for line in logged() if line["message"] == "event loop held"]


async def test_a_loop_held_in_cpu_is_named_by_the_innermost_frame_of_this_project(
    logged: Logged, asyncio_reports: Reported
) -> None:
    log.configure(None)
    with LoopWatch(None):
        holds_through_the_standard_library(HOLD)
        await asyncio.sleep(2 * TICK_SECONDS)
    asyncio_reports.held_on_purpose()

    [line] = held(logged)
    assert line["severity"] == "WARNING"
    assert line["code.function.name"] == f"{__name__}.{holds_through_the_standard_library.__name__}"
    assert isinstance(line["code.line.number"], int)
    assert line["cpu_ms"] >= HOLD * 1000
    assert line["late_ms"] >= (HOLD - TICK_SECONDS) * 1000


async def test_a_loop_held_in_a_wait_is_a_delay_and_is_named_nowhere(
    logged: Logged, asyncio_reports: Reported
) -> None:
    log.configure(None)
    delays: list[float] = []
    with LoopWatch(delays.append):
        waits(HOLD)
        await asyncio.sleep(2 * TICK_SECONDS)
    asyncio_reports.held_on_purpose()

    assert max(delays) >= HOLD - TICK_SECONDS
    assert held(logged) == []


async def test_steps_that_each_use_little_cpu_are_named_nowhere(
    logged: Logged, asyncio_reports: Reported
) -> None:
    log.configure(None)
    with LoopWatch(None):
        until = time.monotonic() + HOLD
        while time.monotonic() < until:
            holds_through_the_standard_library(REPORTED_CPU_SECONDS / 20)
            await asyncio.sleep(REPORTED_CPU_SECONDS / 10)
    asyncio_reports.held_on_purpose()

    assert held(logged) == []


async def test_a_watch_that_ended_names_nothing_and_leaves_no_thread(
    logged: Logged, asyncio_reports: Reported
) -> None:
    log.configure(None)
    threads = threading.active_count()
    with LoopWatch(None):
        await asyncio.sleep(0)
    holds_through_the_standard_library(HOLD)
    await asyncio.sleep(2 * TICK_SECONDS)
    asyncio_reports.held_on_purpose()

    assert threading.active_count() == threads
    assert held(logged) == []
