"""What asyncio's debug mode reports about the event loops a test runs on, and the CPU time
each step on one uses.

`tests/conftest.py` turns debug mode on for every event loop the suite starts, measures every
step, and calls `Reported.check` after each test.
"""

import asyncio
import logging
import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import override

HELD_SECONDS = 1.0
"""The CPU time one step may use on an event loop before its test fails. The time a step waits,
for a CPU or for anything else, does not count."""

_WAITED = "Executing %s took %.3f seconds"
_HELD = "Executing %s used %.3f seconds of CPU"
_UNMEASURED = asyncio.Handle._run


def _measured(self: asyncio.Handle) -> None:
    started = time.thread_time()
    _UNMEASURED(self)
    used = time.thread_time() - started
    if used > HELD_SECONDS:
        logging.getLogger("asyncio").warning(_HELD, self, used)


def measure_every_step() -> None:
    """From now on, each step an event loop in this process runs logs a WARNING on the `asyncio`
    logger when it uses more than `HELD_SECONDS` of CPU. A second call changes nothing."""
    asyncio.Handle._run = _measured


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
                "lifespan. Move that work into a `def` route or into `asyncio.to_thread`.\n"
                + "\n".join(failures)
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
