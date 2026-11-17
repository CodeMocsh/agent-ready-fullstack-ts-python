import pytest

from app.loops import DatabaseGone, rounds
from tests.conftest import Logged
from tests.loops.test_loops import RETRY_AFTER, scripted, unavailable, waits_in


class Stopped(Exception):
    """What a test's round raises to end its loop."""


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


async def test_a_round_the_database_did_not_serve_runs_again_after_its_retry_after(
    lines: Logged,
) -> None:
    each_round, started = scripted([unavailable(), Stopped()])

    with pytest.raises(Stopped):
        await rounds("a loop", each_round)

    assert len(started) == 2
    assert started[1] - started[0] >= RETRY_AFTER
    assert waits_in(lines) == [("a loop", "DatabaseUnavailable", "ConnectionResetError")]
