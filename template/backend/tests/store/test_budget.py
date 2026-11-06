"""The connection budget: each process gets an equal share of what the server allows, and the
template's pool takes what remains of that share. `docs/adr/template/0022`."""

from collections.abc import Callable

import pytest

from app.store.budget import Draws, OverBudget, ServerLimits, budgeted

SERVER = ServerLimits(max_connections=100, reserved=3)


def test_the_pool_takes_what_remains_of_each_process_s_share() -> None:
    draws = Draws(instances=4, in_process={"a feed": 2, "a listener": 1}, outside={"release": 1})

    budget = budgeted(SERVER, draws)

    assert (budget.per_process, budget.pool) == (24, 21)


def test_every_process_together_fits_inside_the_server() -> None:
    draws = Draws(instances=7, in_process={"a feed": 3}, outside={"release": 1, "operator": 2})

    budget = budgeted(SERVER, draws)

    assert draws.instances * (budget.pool + 3) + 3 + SERVER.reserved <= SERVER.max_connections


def test_a_fixed_pool_is_held_to_the_share() -> None:
    assert budgeted(SERVER, Draws(instances=1, in_process={}, outside={}, pool=1)).pool == 1

    with pytest.raises(OverBudget, match="the pool needs 98"):
        budgeted(SERVER, Draws(instances=1, in_process={}, outside={}, pool=98))


def test_draws_that_leave_the_pool_nothing_refuse_to_start() -> None:
    draws = Draws(instances=12, in_process={"a feed": 10, "a listener": 2}, outside={})

    with pytest.raises(OverBudget) as refused:
        budgeted(SERVER, draws)

    assert "That leaves 8 for each of 12 processes" in str(refused.value)
    assert "a feed: 10, a listener: 2" in str(refused.value)
    assert "DATABASE_INSTANCES" in str(refused.value)


@pytest.mark.parametrize(
    "draws",
    [
        lambda: Draws(instances=0, in_process={}, outside={}),
        lambda: Draws(instances=1, in_process={"a feed": -1}, outside={}),
        lambda: Draws(instances=1, in_process={}, outside={"release": -1}),
        lambda: Draws(instances=1, in_process={"release": -1}, outside={"release": 1}),
        lambda: Draws(instances=1, in_process={}, outside={}, pool=0),
    ],
    ids=[
        "no-instance",
        "a-negative-draw-in-process",
        "a-negative-draw-outside",
        "a-negative-draw-named-again-outside",
        "an-empty-pool",
    ],
)
def test_draws_that_hold_no_connection_are_refused(draws: Callable[[], Draws]) -> None:
    with pytest.raises(ValueError, match="no connection|fewer than no|lends none"):
        draws()
