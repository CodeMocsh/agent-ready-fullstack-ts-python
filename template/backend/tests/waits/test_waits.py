"""Each wait in a request has a bound under the request budget. A wait that may last longer
runs beside the requests, and work beside the requests reports how it ended. Shared work runs one
run at a time for each key, for the callers that await it, and each wait in a run has a bound
under the budget."""

import asyncio
import math

import pytest
from fastapi import APIRouter, BackgroundTasks
from fastapi.testclient import TestClient

from app import log, waits
from app.audit_events import acting_as, current_actor
from app.identity.shared import Actor
from app.store import Database
from app.waits import (
    REQUEST_BUDGET_SECONDS,
    BesideOutlastsTheBudget,
    BesideOutlivedItsCancel,
    PartWaitsOutside,
    SharedWork,
    WaitOutlastsTheRequest,
    WaitTimedOut,
    beside,
    cancel_beside,
    waiting,
)
from tests.conftest import Logged
from tests.routed import routed

PAST_THE_BUDGET = REQUEST_BUDGET_SECONDS + 5
UNDER_THE_BUDGET = REQUEST_BUDGET_SECONDS - 0.01
WORK = "a check on a customer's database"
SHARED = "a draw of the plan"
ACTOR = Actor(issuer="a-test", subject="the-checker", name="The checker")

router = APIRouter()


async def waits_for(seconds: float) -> None:
    async with waiting(seconds, on="a customer's database"):
        pass


async def waits_past_the_budget() -> None:
    await waits_for(PAST_THE_BUDGET)


@router.post("/in-the-request", status_code=204)
async def in_the_request(seconds: float) -> None:
    await waits_for(seconds)


@router.post("/in-a-task-of-the-request", status_code=204)
async def in_a_task_of_the_request() -> None:
    await asyncio.create_task(waits_past_the_budget())


@router.post("/in-a-background-task", status_code=202)
async def in_a_background_task(background: BackgroundTasks) -> None:
    background.add_task(waits_past_the_budget)


@router.post("/beside-the-request", status_code=202)
async def beside_the_request() -> None:
    task = beside(waits_past_the_budget(), name=WORK, within=UNDER_THE_BUDGET)
    async with waiting(1.0, on="the work beside the request"):
        _ = await asyncio.wait([task])
    task.result()


async def fails() -> None:
    raise ConnectionRefusedError("the customer's database refused the login")


async def waits_for_ever() -> None:
    _ = await asyncio.Event().wait()


@pytest.mark.parametrize("seconds", [REQUEST_BUDGET_SECONDS, PAST_THE_BUDGET])
def test_a_request_refuses_a_wait_whose_bound_is_not_under_the_budget(
    database: Database, seconds: float
) -> None:
    with (
        TestClient(routed(router, database=database)) as client,
        pytest.raises(WaitOutlastsTheRequest, match="app/jobs.py"),
    ):
        _ = client.post("/in-the-request", params={"seconds": seconds})


def test_a_request_starts_a_wait_whose_bound_is_under_the_budget(database: Database) -> None:
    with TestClient(routed(router, database=database)) as client:
        answered = client.post("/in-the-request", params={"seconds": UNDER_THE_BUDGET})

    assert answered.status_code == 204


@pytest.mark.parametrize("route", ["/in-a-task-of-the-request", "/in-a-background-task"])
def test_a_task_the_request_starts_itself_is_still_in_the_request(
    database: Database, route: str
) -> None:
    with (
        TestClient(routed(router, database=database)) as client,
        pytest.raises(WaitOutlastsTheRequest),
    ):
        _ = client.post(route)


def test_the_same_wait_beside_the_request_passes(database: Database) -> None:
    with TestClient(routed(router, database=database)) as client:
        assert client.post("/beside-the-request").status_code == 202


async def test_the_same_wait_outside_a_request_passes() -> None:
    await waits_past_the_budget()


@pytest.mark.parametrize("seconds", [0.0, -1.0, math.inf, math.nan])
async def test_a_bound_that_is_not_a_positive_number_of_seconds_is_refused(seconds: float) -> None:
    with pytest.raises(ValueError, match="not a bound"):
        await waits_for(seconds)


async def test_a_wait_past_its_bound_is_cancelled_and_names_what_it_waited_on() -> None:
    with pytest.raises(WaitTimedOut, match="a customer's database"):
        async with waiting(0.01, on="a customer's database"):
            await asyncio.sleep(1)


async def test_a_task_beside_the_requests_acts_as_the_actor_that_started_it() -> None:
    acted_as: list[Actor | None] = []

    async def records_its_actor() -> None:
        acted_as.append(current_actor())

    with acting_as(ACTOR):
        task = beside(records_its_actor(), name=WORK, within=UNDER_THE_BUDGET)
    _ = await asyncio.wait([task])

    assert acted_as == [ACTOR]


async def test_a_task_beside_the_requests_that_fails_writes_its_traceback(logged: Logged) -> None:
    log.configure(None)

    _ = await asyncio.wait([beside(fails(), name=WORK, within=UNDER_THE_BUDGET)])

    [line] = [one for one in logged() if one["message"] == "work beside the requests failed"]
    assert line["severity"] == "ERROR"
    assert line["work"] == WORK
    assert "ConnectionRefusedError" in line["exception"]


async def test_shutdown_cancels_the_tasks_beside_the_requests_and_names_each(
    logged: Logged,
) -> None:
    log.configure(None)
    task = beside(waits_for_ever(), name=WORK, within=UNDER_THE_BUDGET)
    await asyncio.sleep(0)

    await cancel_beside()

    assert task.cancelled()
    [line] = [one for one in logged() if one["message"] == "work beside the requests cancelled"]
    assert line["severity"] == "WARNING"
    assert line["work"] == WORK


async def test_shutdown_names_a_task_that_outlives_its_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    released = asyncio.Event()

    async def ends_only_when_released() -> None:
        try:
            await waits_for_ever()
        except asyncio.CancelledError:
            await released.wait()

    monkeypatch.setattr(waits, "CANCELLED_WITHIN_SECONDS", 0.01)
    task = beside(ends_only_when_released(), name=WORK, within=UNDER_THE_BUDGET)
    await asyncio.sleep(0)

    with pytest.raises(BesideOutlivedItsCancel, match=WORK):
        async with asyncio.timeout(1):
            await cancel_beside()

    released.set()
    _ = await asyncio.wait([task])


async def test_a_task_beside_the_requests_ends_with_the_result_of_its_work() -> None:
    async def answers() -> str:
        return "the image"

    assert await beside(answers(), name=WORK, within=UNDER_THE_BUDGET) == "the image"


class Draws:
    def __init__(self) -> None:
        self.started: int = 0
        self.released: asyncio.Event = asyncio.Event()

    async def draw(self) -> str:
        self.started += 1
        _ = await self.released.wait()
        return f"draw {self.started}"


async def test_callers_that_arrive_while_a_run_is_in_flight_await_that_run() -> None:
    shared: SharedWork[str, str] = SharedWork(SHARED)
    draws = Draws()

    async with asyncio.timeout(1), asyncio.TaskGroup() as callers:
        first = callers.create_task(shared.joined("plan", draws.draw))
        second = callers.create_task(shared.joined("plan", draws.draw))
        await asyncio.sleep(0)
        draws.released.set()

    assert (first.result(), second.result(), draws.started) == ("draw 1", "draw 1", 1)


async def test_a_call_after_a_run_ends_starts_another() -> None:
    shared: SharedWork[str, str] = SharedWork(SHARED)
    draws = Draws()
    draws.released.set()

    assert await shared.joined("plan", draws.draw) == "draw 1"
    assert await shared.joined("plan", draws.draw) == "draw 2"


async def test_a_run_goes_on_for_the_others_when_one_caller_is_cancelled() -> None:
    shared: SharedWork[str, str] = SharedWork(SHARED)
    draws = Draws()

    async with asyncio.timeout(1), asyncio.TaskGroup() as callers:
        cancelled = callers.create_task(shared.joined("plan", draws.draw))
        stays = callers.create_task(shared.joined("plan", draws.draw))
        await asyncio.sleep(0)
        _ = cancelled.cancel()
        await asyncio.sleep(0)
        draws.released.set()

    assert (cancelled.cancelled(), stays.result()) == (True, "draw 1")


async def test_shared_work_started_outside_a_request_is_held_to_the_budget() -> None:
    shared: SharedWork[str, None] = SharedWork(SHARED)

    with pytest.raises(WaitOutlastsTheRequest):
        await shared.joined("plan", waits_past_the_budget)


async def test_shared_work_acts_as_the_actor_that_started_it() -> None:
    shared: SharedWork[str, Actor | None] = SharedWork(SHARED)

    async def its_actor() -> Actor | None:
        return current_actor()

    with acting_as(ACTOR):
        assert await shared.joined("plan", its_actor) == ACTOR


async def test_a_failure_of_shared_work_raises_in_each_caller_and_writes_its_traceback(
    logged: Logged,
) -> None:
    log.configure(None)
    shared: SharedWork[str, None] = SharedWork(SHARED)

    failures = await asyncio.gather(
        shared.joined("plan", fails), shared.joined("plan", fails), return_exceptions=True
    )

    assert [type(failure) for failure in failures] == [ConnectionRefusedError] * 2
    [line] = [one for one in logged() if one["message"] == "shared work failed"]
    assert line["severity"] == "ERROR"
    assert line["work"] == SHARED
    assert "ConnectionRefusedError" in line["exception"]


async def test_a_run_that_joins_its_own_key_is_refused() -> None:
    shared: SharedWork[str, None] = SharedWork(SHARED)

    async def joins_its_own_key() -> None:
        await shared.joined("plan", joins_its_own_key)

    with pytest.raises(RuntimeError, match="its own key"):
        async with asyncio.timeout(1):
            await shared.joined("plan", joins_its_own_key)


async def test_shutdown_cancels_shared_work_still_in_flight_and_names_it(logged: Logged) -> None:
    log.configure(None)
    shared: SharedWork[str, None] = SharedWork(SHARED)
    caller = asyncio.create_task(shared.joined("plan", waits_for_ever))
    await asyncio.sleep(0)

    await cancel_beside()

    with pytest.raises(asyncio.CancelledError):
        async with asyncio.timeout(1):
            await caller
    [line] = [one for one in logged() if one["message"] == "shared work cancelled"]
    assert line["severity"] == "WARNING"
    assert line["work"] == SHARED


@pytest.mark.parametrize("within", [REQUEST_BUDGET_SECONDS, PAST_THE_BUDGET])
async def test_work_beside_the_requests_whose_bound_is_not_under_the_budget_is_refused(
    within: float,
) -> None:
    with pytest.raises(BesideOutlastsTheBudget, match="app/jobs.py"):
        _ = beside(waits_for_ever(), name=WORK, within=within)


@pytest.mark.parametrize("within", [0.0, -1.0, math.inf, math.nan])
async def test_work_beside_the_requests_whose_bound_is_not_a_bound_is_refused(
    within: float,
) -> None:
    with pytest.raises(ValueError, match="not a bound"):
        _ = beside(waits_for_ever(), name=WORK, within=within)


async def test_work_beside_the_requests_past_its_bound_is_cancelled_and_reported(
    logged: Logged,
) -> None:
    log.configure(None)

    task = beside(waits_for_ever(), name=WORK, within=0.01)
    _ = await asyncio.wait([task])

    assert isinstance(task.exception(), WaitTimedOut)
    [line] = [one for one in logged() if one["message"] == "work beside the requests failed"]
    assert line["work"] == WORK


async def test_a_wait_in_a_part_is_refused_before_it_starts() -> None:
    started = False

    with waits.in_a_part(), pytest.raises(PartWaitsOutside, match="sqlserver.login"):
        async with waiting(1.0, on="sqlserver.login"):
            started = True

    assert not started


async def test_work_beside_the_requests_that_a_part_starts_is_not_a_part() -> None:
    async def waits_briefly() -> str:
        async with waiting(1.0, on="beside.call"):
            return "waited"

    with waits.in_a_part():
        work = beside(waits_briefly(), name="beside a part", within=1.0)

    assert await work == "waited"
