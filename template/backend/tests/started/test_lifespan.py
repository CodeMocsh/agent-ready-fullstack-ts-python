"""The lifespan builds the routes before the first request, and cancels the work beside the
requests before it closes the database."""

import asyncio

import pytest
from fastapi import routing
from fastapi.testclient import TestClient

from app import lifespan, main, waits
from app.deployment import ADMISSION_MODE_ENV
from app.waits import REQUEST_BUDGET_SECONDS, BesideOutlivedItsCancel, WaitTimedOut, beside
from tests.clock import elapse
from tests.conftest import Logged

UNDER_THE_BUDGET = REQUEST_BUDGET_SECONDS - 0.01


def _refused(**named: object) -> None:
    raise AssertionError(f"a request built the route at {named['path']}")


def test_the_routes_are_built_before_the_first_request(monkeypatch: pytest.MonkeyPatch) -> None:
    with TestClient(main.create_app()) as serving:
        monkeypatch.setattr(routing, "get_dependant", _refused)

        assert serving.get("/health").status_code == 200


def test_a_request_to_an_app_whose_lifespan_never_ran_builds_a_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routing, "get_dependant", _refused)

    with pytest.raises(AssertionError, match="a request built the route"):
        TestClient(main.create_app()).get("/health")


def test_shutdown_cancels_the_work_beside_the_requests_before_the_database_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ended: list[str] = []

    async def works_until_cancelled() -> None:
        try:
            _ = await asyncio.Event().wait()
        finally:
            ended.append("the work")

    async def starts_the_work() -> None:
        _ = beside(works_until_cancelled(), name="work until shutdown", within=UNDER_THE_BUDGET)

    app = main.create_app()
    with TestClient(app) as serving:
        database = app.state.database
        closes = database.close

        async def records_the_close() -> None:
            ended.append("the database")
            await closes()

        monkeypatch.setattr(database, "close", records_the_close)
        assert serving.portal is not None
        serving.portal.call(starts_the_work)

    assert ended == ["the work", "the database"]


def test_a_task_that_outlives_its_cancel_fails_the_shutdown_and_the_database_still_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[bool] = []

    async def ends_a_second_after_its_cancel() -> None:
        try:
            _ = await asyncio.Event().wait()
        except asyncio.CancelledError:
            await elapse(1)

    async def starts_the_work() -> None:
        _ = beside(
            ends_a_second_after_its_cancel(), name="ignores its cancel", within=UNDER_THE_BUDGET
        )

    monkeypatch.setattr(waits, "CANCELLED_WITHIN_SECONDS", 0.01)
    app = main.create_app()
    with (
        pytest.raises(BesideOutlivedItsCancel, match="ignores its cancel"),
        TestClient(app) as serving,
    ):
        closes = app.state.database.close

        async def records_the_close() -> None:
            closed.append(True)
            await closes()

        monkeypatch.setattr(app.state.database, "close", records_the_close)
        assert serving.portal is not None
        serving.portal.call(starts_the_work)

    assert closed == [True]


def test_a_database_that_does_not_close_in_time_fails_the_shutdown_within_its_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def closes_after_a_second() -> None:
        await elapse(1)

    monkeypatch.setattr(lifespan, "CLOSED_WITHIN_SECONDS", 0.01)
    app = main.create_app()
    with pytest.raises(WaitTimedOut, match="database.close"), TestClient(app):
        monkeypatch.setattr(app.state.database, "close", closes_after_a_second)


def test_the_process_says_what_it_admits_and_warns_when_it_only_reports(
    monkeypatch: pytest.MonkeyPatch, logged: Logged
) -> None:
    monkeypatch.setenv(ADMISSION_MODE_ENV, "evaluate")

    with TestClient(main.create_app()):
        said = [one for one in logged() if one["message"] == "admission"]

    assert [(one["severity"], one["enforced"]) for one in said] == [("WARNING", False)]


def test_the_process_says_what_a_group_of_reads_runs(logged: Logged) -> None:
    app = main.create_app()
    with TestClient(app):
        said = [one for one in logged() if one["message"] == "reads"]

    limits = app.state.read_limits
    assert [
        (one["part_bound_seconds"], one["max_parts"], one["part_concurrency"]) for one in said
    ] == [(limits.part_bound_seconds, limits.max_parts, limits.part_concurrency)]
