"""Every wait on time in the app, each named for why it waits.

ruff refuses a bare `asyncio.sleep`, `anyio.sleep` and `time.sleep` in each module that
`per-file-ignores` does not exempt from `TID251`. This module and `tests/clock.py` are the two
exempt for that reason alone. A wait on time is right for a loop on a period, a retry, a turn of
the event loop, and a system outside the process that has no push. A wait on rows that this
system writes is never a wait on time: it is a wake, as `pg_notify` wakes the worker.
`docs/adr/template/0027`.
"""

import asyncio
import math
from collections.abc import AsyncIterator, Awaitable, Callable

from app.waits import waiting


def _refuse_unbounded(seconds: float, said: str) -> None:
    if not 0 < seconds < math.inf:
        raise ValueError(f"{said} is {seconds} s, which is not a positive number of seconds")


async def every(period: float) -> AsyncIterator[None]:
    """Yield once each `period` seconds, the first time after one period, for a loop that runs
    on a period. Raises `ValueError` on a period that is not a positive number of seconds."""
    _refuse_unbounded(period, "the period")
    while True:
        await asyncio.sleep(period)
        yield


async def backoff(seconds: float) -> None:
    """Wait `seconds` before a call is tried again, as the `Retry-After` of a refusal says.
    Raises `ValueError` on a wait that is not a positive number of seconds."""
    _refuse_unbounded(seconds, "the wait before a retry")
    await asyncio.sleep(seconds)


async def yield_now() -> None:
    """Give each other task on the event loop a turn, and wait for nothing else."""
    await asyncio.sleep(0)


async def poll_outside[T](
    check: Callable[[], Awaitable[T | None]], *, period: float, within: float, on: str
) -> T:
    """The first answer of `check` that is not `None`, asking each `period` seconds, for a system
    outside the process that has no push. Raises `WaitTimedOut`, which names `on`, after
    `within` seconds. The bound is `app.waits.waiting`'s, so in a request it is under the
    request budget."""
    _refuse_unbounded(period, "the period")
    async with waiting(within, on=on):
        while (answer := await check()) is None:
            await asyncio.sleep(period)
        return answer
