"""The waits on time a test makes, each named for why it waits. ruff refuses a bare sleep
everywhere but here and `app/pacing.py`. `docs/adr/template/0027`."""

import asyncio
import time


async def elapse(seconds: float) -> None:
    """Let `seconds` pass on the event loop, as a test waits for a lease, a tick or a bound to
    end."""
    await asyncio.sleep(seconds)


def hold_the_thread(seconds: float) -> None:
    """Block the calling thread for `seconds`, as a test holds the event loop or a worker thread
    on purpose."""
    time.sleep(seconds)
