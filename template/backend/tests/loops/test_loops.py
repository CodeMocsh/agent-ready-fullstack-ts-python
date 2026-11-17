"""A loop waits for the database within a bound, and stops its process past it.
`docs/adr/template/0027`."""

import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Final

import pytest

from app.loops import rounds
from app.store import DatabaseUnavailable
from tests.conftest import Logged

RETRY_AFTER: Final = 1


def unavailable() -> DatabaseUnavailable:
    lost = DatabaseUnavailable("the connection ended", retry_after=RETRY_AFTER)
    lost.__cause__ = ConnectionResetError("the server closed the socket")
    return lost


def scripted(
    turns: Iterable[Exception | None],
) -> tuple[Callable[[], Awaitable[None]], list[float]]:
    """A round that raises each exception of `turns` in turn and ends on each `None`, and the
    time each run started."""
    left = iter(turns)
    started: list[float] = []

    async def each_round() -> None:
        started.append(time.monotonic())
        turn = next(left)
        if turn is not None:
            raise turn

    return each_round, started


def waits_in(logged: Logged) -> list[tuple[str, str, str | None]]:
    return [
        (line["loop"], line["refusal"], line["cause"])
        for line in logged()
        if line["message"] == "loop waits for the database"
    ]


async def test_any_other_error_stops_the_loop_at_once(lines: Logged) -> None:
    each_round, started = scripted([ValueError("a bug")])

    with pytest.raises(ValueError, match="a bug"):
        await rounds("a loop", each_round)

    assert len(started) == 1
    assert waits_in(lines) == []
