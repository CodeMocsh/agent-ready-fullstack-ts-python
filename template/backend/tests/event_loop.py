"""What asyncio's debug mode reports about the event loops a test runs on.

`tests/conftest.py` turns debug mode on for every event loop the suite starts, and calls
`Reported.check` after each test.
"""

import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import override

HELD_SECONDS = 1.0
_HELD_STEP = "Executing %s took %.3f seconds"


class Reported(logging.Handler):
    """Every record the `asyncio` logger passes to it while it is installed."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    @override
    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def failures(self) -> list[str]:
        """Each record, except a step that held an event loop for `HELD_SECONDS` or less."""
        return [one.getMessage() for one in self.records if not _held_briefly(one)]

    def held_on_purpose(self) -> list[str]:
        """Each step that held an event loop so far, then removed, for a test that holds one on
        purpose. Every other record stays."""
        held = [one for one in self.records if one.msg == _HELD_STEP]
        self.records = [one for one in self.records if one.msg != _HELD_STEP]
        return [one.getMessage() for one in held]

    def check(self) -> None:
        """Raises on each failure since the last check. Removes every record either way."""
        failures = self.failures()
        self.records = []
        if failures:
            raise AssertionError(
                "asyncio logged while this test ran. A line that says a step took too long names "
                "a step that did blocking work on the event loop: CPU work, or a call that waits "
                "without `await`. Move that work into a `def` route or into `asyncio.to_thread`.\n"
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


def _held_briefly(record: logging.LogRecord) -> bool:
    match record.args:
        case (_, float() as seconds) if record.msg == _HELD_STEP:
            return seconds <= HELD_SECONDS
        case _:
            return False
