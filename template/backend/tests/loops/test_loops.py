"""A loop waits for the database within a bound, and stops its process past it.
`docs/adr/template/0027`."""

import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Final

import pytest

from app import log
from app.loops import DatabaseGone, rounds
from app.store import DatabaseUnavailable
from tests.conftest import Logged

RETRY_AFTER: Final = 1


class Stopped(Exception):
    """What a test's round raises to end its loop."""


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


@pytest.fixture
def lines(logged: Logged) -> Logged:
    log.configure(None)
    return logged


def waits_in(logged: Logged) -> list[tuple[str, str, str | None]]:
    return [
        (line["loop"], line["refusal"], line["cause"])
        for line in logged()
        if line["message"] == "loop waits for the database"
    ]


async def test_a_round_the_database_did_not_serve_runs_again_after_its_retry_after(
    lines: Logged,
) -> None:
    each_round, started = scripted([unavailable(), Stopped()])

    with pytest.raises(Stopped):
        await rounds("a loop", each_round)

    assert len(started) == 2
    assert started[1] - started[0] >= RETRY_AFTER
    assert waits_in(lines) == [("a loop", "DatabaseUnavailable", "ConnectionResetError")]


async def test_a_database_unavailable_past_the_bound_stops_the_loop() -> None:
    first, second = unavailable(), unavailable()
    each_round, started = scripted([first, second])

    with pytest.raises(DatabaseGone, match="the loop 'a loop' found the database") as gone:
        await rounds("a loop", each_round, within=RETRY_AFTER / 2)

    assert len(started) == 2
    assert gone.value.__cause__ is second


async def test_a_round_that_ends_starts_the_bound_again() -> None:
    each_round, started = scripted([unavailable(), None, unavailable(), unavailable(), Stopped()])

    with pytest.raises(Stopped):
        await rounds("a loop", each_round, within=RETRY_AFTER * 1.2)

    assert len(started) == 5


async def test_any_other_error_stops_the_loop_at_once(lines: Logged) -> None:
    each_round, started = scripted([ValueError("a bug")])

    with pytest.raises(ValueError, match="a bug"):
        await rounds("a loop", each_round)

    assert len(started) == 1
    assert waits_in(lines) == []
