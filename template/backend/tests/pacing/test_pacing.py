"""Each wait on time names why it waits, and each refuses a wait that has no bound.
`docs/adr/template/0027`."""

import math

import pytest

from app.pacing import backoff, every, poll_outside
from app.waits import WaitTimedOut


async def test_a_loop_on_a_period_runs_once_each_period() -> None:
    ran = 0
    async for _ in every(0.01):
        ran += 1
        if ran == 3:
            break

    assert ran == 3


@pytest.mark.parametrize("seconds", [0.0, -1.0, math.inf, math.nan])
async def test_a_wait_that_is_not_a_positive_number_of_seconds_is_refused(seconds: float) -> None:
    with pytest.raises(ValueError, match="not a positive number of seconds"):
        await backoff(seconds)
    with pytest.raises(ValueError, match="not a positive number of seconds"):
        _ = await anext(every(seconds))


async def test_a_poll_answers_the_first_answer_of_its_check() -> None:
    answers = iter([None, None, "ready"])

    async def check() -> str | None:
        return next(answers)

    assert await poll_outside(check, period=0.01, within=1.0, on="an image build") == "ready"


async def test_a_poll_past_its_bound_names_what_it_waited_on() -> None:
    async def never() -> str | None:
        return None

    with pytest.raises(WaitTimedOut, match="an image build"):
        _ = await poll_outside(never, period=0.01, within=0.05, on="an image build")
