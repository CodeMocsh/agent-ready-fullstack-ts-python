"""Each wait in a request has a bound under the request budget. A wait that may last longer
runs beside the requests, and work beside the requests reports how it ended."""

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
    BesideOutlivedItsCancel,
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
    task = beside(waits_past_the_budget(), name=WORK)
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
        pytest.raises(WaitOutlastsTheRequest, match="app.waits.beside"),
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
        task = beside(records_its_actor(), name=WORK)
    _ = await asyncio.wait([task])

    assert acted_as == [ACTOR]


async def test_a_task_beside_the_requests_that_fails_writes_its_traceback(logged: Logged) -> None:
    log.configure(None)

    _ = await asyncio.wait([beside(fails(), name=WORK)])

    [line] = [one for one in logged() if one["message"] == "work beside the requests failed"]
    assert line["severity"] == "ERROR"
    assert line["work"] == WORK
    assert "ConnectionRefusedError" in line["exception"]


async def test_shutdown_cancels_the_tasks_beside_the_requests_and_names_each(
    logged: Logged,
) -> None:
    log.configure(None)
    task = beside(waits_for_ever(), name=WORK)
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
    task = beside(ends_only_when_released(), name=WORK)
    await asyncio.sleep(0)

    with pytest.raises(BesideOutlivedItsCancel, match=WORK):
        async with asyncio.timeout(1):
            await cancel_beside()

    released.set()
    _ = await asyncio.wait([task])
