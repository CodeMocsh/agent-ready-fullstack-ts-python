"""What asyncio's debug mode reports about the event loops a test runs on.

`tests/conftest.py` turns debug mode on for every event loop the suite starts, and fails a test on
each line `Reported.failures` returns.
"""

import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import override

HELD_SECONDS = 1.0
_HELD_STEP = "Executing %s took %.3f seconds"


class Reported(logging.Handler):
    """Every record on the `asyncio` logger while it is installed."""

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
        """What asyncio logged so far, then removed, for a test that holds an event loop on
        purpose."""
        said = [one.getMessage() for one in self.records]
        self.records.clear()
        return said


@contextmanager
def reported() -> Generator[Reported]:
    """Every record on the `asyncio` logger inside the block, kept out of the log the app writes
    to stdout. Restores the logger after."""
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
