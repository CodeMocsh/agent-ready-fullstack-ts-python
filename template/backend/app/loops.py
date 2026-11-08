"""Each loop of a process runs through `rounds`, which owns what a loop does in a database outage.

A loop runs one round again and again until its process stops. A round that raises
`DatabaseUnavailable` waits the refusal's `Retry-After` and runs again. A database that stays
unavailable for `UNAVAILABLE_WITHIN_SECONDS` raises `DatabaseGone`, and the loop's process stops.
Any other error raises at once. `docs/adr/template/0027`.
"""

import time
from collections.abc import Awaitable, Callable
from typing import Final, Never

from app import log
from app.pacing import backoff
from app.store import DatabaseUnavailable

UNAVAILABLE_WITHIN_SECONDS: Final = 300.0
"""The longest a loop waits for the database before its process stops."""


class DatabaseGone(RuntimeError):
    """A loop found the database unavailable for longer than its bound."""


async def rounds(
    loop: str,
    each_round: Callable[[], Awaitable[None]],
    *,
    within: float = UNAVAILABLE_WITHIN_SECONDS,
) -> Never:
    """Run `each_round` again and again. After a round that raises `DatabaseUnavailable`, wait its
    `retry_after` and run the next. Raises `DatabaseGone`, which names `loop`, once rounds have
    raised `DatabaseUnavailable` for longer than `within` seconds with no round ending between.
    Any other error raises at once.

    Give it a round that raises `DatabaseUnavailable` only when the database served it nothing,
    and that ends when the database served it and then stopped."""
    since: float | None = None
    while True:
        try:
            await each_round()
        except DatabaseUnavailable as unavailable:
            now = time.monotonic()
            since = now if since is None else since
            if now - since > within:
                raise DatabaseGone(
                    f"the loop {loop!r} found the database unavailable for {now - since:.0f} s, "
                    f"past its bound of {within:.0f} s. The process stops, so that its platform "
                    f"starts it again."
                ) from unavailable
            log.loop_waits(
                loop=loop,
                refusal=type(unavailable).__name__,
                cause=unavailable.cause,
                unavailable_seconds=now - since,
            )
            await backoff(unavailable.retry_after)
        else:
            since = None
