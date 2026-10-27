"""What asyncio's debug mode reports about the event loops a test runs on, and the CPU time
each step on one uses.

`tests/conftest.py` turns debug mode on for every event loop the suite starts, measures every
step, and calls `Reported.check` after each test.
"""

import asyncio
import logging
import statistics
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import override

HELD_SECONDS = 1.0
"""The CPU time one step may use on an event loop before its test fails, in seconds on the
reference core. The time a step waits, for a CPU or for anything else, does not count."""

REFERENCE_WORKLOAD_SECONDS = 0.246
"""The CPU time `workload` uses on the reference core: one core of a Cloud Build `E2_HIGHCPU_32`
machine, in `python:3.12-slim` on x86_64, Python 3.12.16. The least of 7 runs."""

WORKLOAD_RUNS = 5
"""How many times `step_limit_on_this_machine` runs `workload`."""

_WAITED = "Executing %s took %.3f seconds"
_HELD = "Executing %s used %.3f seconds of CPU, which is %.3f seconds on the reference core"
_UNMEASURED = asyncio.Handle._run


def workload() -> int:
    """Fixed CPU work in pure Python. `REFERENCE_WORKLOAD_SECONDS` is true for this code only."""
    total = 0
    held: dict[str, list[int]] = {}
    for i in range(400_000):
        key = f"k{i % 5000}"
        held.setdefault(key, []).append(i)
        total += len(key) * (i % 7)
    for values in held.values():
        total += sum(sorted(values, reverse=True)[:3])
    return total


@dataclass(frozen=True)
class StepLimit:
    """The CPU time one step may use on a machine that runs `workload` in `workload_seconds` of
    CPU."""

    workload_seconds: float

    @property
    def seconds(self) -> float:
        """`HELD_SECONDS` on the reference core, in seconds of CPU on this machine."""
        return HELD_SECONDS * self.workload_seconds / REFERENCE_WORKLOAD_SECONDS

    def on_the_reference_core(self, seconds: float) -> float:
        """What `seconds` of CPU on this machine comes to on the reference core."""
        return seconds * REFERENCE_WORKLOAD_SECONDS / self.workload_seconds


def cpu_seconds(work: Callable[[], object]) -> float:
    """The CPU time this thread uses to run `work` once."""
    started = time.thread_time()
    work()
    return time.thread_time() - started


def step_limit_on_this_machine() -> StepLimit:
    """Runs `workload` `WORKLOAD_RUNS` times on this thread, now, and takes the median CPU time.
    Blocks for about `WORKLOAD_RUNS` times `REFERENCE_WORKLOAD_SECONDS` on the reference core."""
    return StepLimit(statistics.median(cpu_seconds(workload) for _ in range(WORKLOAD_RUNS)))


def measure_every_step(limit: StepLimit) -> None:
    """From now on, each step an event loop in this process runs logs a WARNING on the `asyncio`
    logger when it uses more than `limit.seconds` of CPU. The line names the CPU time on this
    machine and on the reference core. A second call replaces the limit."""

    def measured(self: asyncio.Handle) -> None:
        started = time.thread_time()
        _UNMEASURED(self)
        used = time.thread_time() - started
        if used > limit.seconds:
            logging.getLogger("asyncio").warning(
                _HELD, self, used, limit.on_the_reference_core(used)
            )

    asyncio.Handle._run = measured


def hold_the_event_loop(seconds: float) -> None:
    """Uses `seconds` of CPU without returning to the event loop: the step a test holds on
    purpose."""
    until = time.thread_time() + seconds
    while time.thread_time() < until:
        pass


class Reported(logging.Handler):
    """Every record the `asyncio` logger passes to it while it is installed."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    @override
    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def failures(self) -> list[str]:
        """Each record, except asyncio's own line that a step took long in wall time."""
        return [one.getMessage() for one in self.records if one.msg != _WAITED]

    def held_on_purpose(self) -> list[str]:
        """Each step that held an event loop so far, in CPU or in wall time, then removed, for a
        test that holds one on purpose. Every other record stays."""
        held = [one for one in self.records if one.msg in (_WAITED, _HELD)]
        self.records = [one for one in self.records if one.msg not in (_WAITED, _HELD)]
        return [one.getMessage() for one in held]

    def check(self) -> None:
        """Raises on each failure since the last check. Removes every record either way."""
        failures = self.failures()
        self.records = []
        if failures:
            raise AssertionError(
                "asyncio logged while this test ran. A line that says a step used seconds of CPU "
                "names a step that did CPU work on the event loop, in a route, a fixture or the "
                "lifespan. Move that work into a `def` route or into `asyncio.to_thread`. The "
                f"limit is {HELD_SECONDS} s of CPU on the reference core, a Google Cloud E2 "
                "core. The line says what the step uses there.\n" + "\n".join(failures)
            )


@contextmanager
def reported() -> Generator[Reported]:
    """Every record from WARNING up on the `asyncio` logger inside the block, kept out of the log
    the app writes to stdout. Restores the logger after."""
    logger = logging.getLogger("asyncio")
    handler = Reported()
    level, propagate = logger.level, logger.propagate
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    logger.propagate = False
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)
        logger.propagate = propagate
