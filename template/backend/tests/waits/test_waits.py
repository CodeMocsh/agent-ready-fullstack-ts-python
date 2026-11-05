"""A request waits on one system for less than the request budget. A wait that may last longer
runs beside the requests, and a task beside the requests reports how it ended."""

import asyncio
import math

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app import log
from app.store import Database
from app.waits import (
    REQUEST_BUDGET_SECONDS,
    WaitOutlastsTheRequest,
    WaitTimedOut,
    beside,
    cancel_beside,
    waiting,
)
from tests.conftest import Logged
from tests.routed import routed

PAST_THE_BUDGET = REQUEST_BUDGET_SECONDS + 5
WORK = "a check on a customer's database"

router = APIRouter()


async def waits_past_the_budget() -> None:
    async with waiting(PAST_THE_BUDGET, on="a customer's database"):
        pass


@router.post("/in-the-request", status_code=204)
async def in_the_request() -> None:
    await waits_past_the_budget()


@router.post("/in-a-task-of-the-request", status_code=204)
async def in_a_task_of_the_request() -> None:
    await asyncio.create_task(waits_past_the_budget())


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


def test_a_request_refuses_a_wait_past_the_budget(database: Database) -> None:
    with (
        TestClient(routed(router, database=database)) as client,
        pytest.raises(WaitOutlastsTheRequest, match="app.waits.beside"),
    ):
        _ = client.post("/in-the-request")


def test_a_task_the_request_starts_itself_is_still_in_the_request(database: Database) -> None:
    with (
        TestClient(routed(router, database=database)) as client,
        pytest.raises(WaitOutlastsTheRequest),
    ):
        _ = client.post("/in-a-task-of-the-request")


def test_the_same_wait_beside_the_request_passes(database: Database) -> None:
    with TestClient(routed(router, database=database)) as client:
        assert client.post("/beside-the-request").status_code == 202


async def test_the_same_wait_outside_a_request_passes() -> None:
    await waits_past_the_budget()


@pytest.mark.parametrize("seconds", [0.0, -1.0, math.inf, math.nan])
async def test_a_bound_that_is_not_a_positive_number_of_seconds_is_refused(seconds: float) -> None:
    with pytest.raises(ValueError, match="not a bound"):
        async with waiting(seconds, on="a customer's database"):
            pass


async def test_a_wait_past_its_bound_is_cancelled_and_names_what_it_waited_on() -> None:
    with pytest.raises(WaitTimedOut, match="a customer's database"):
        async with waiting(0.01, on="a customer's database"):
            await asyncio.sleep(1)


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
