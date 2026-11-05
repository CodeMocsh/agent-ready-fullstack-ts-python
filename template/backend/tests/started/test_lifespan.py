"""The lifespan builds the routes before the first request, and cancels the work beside the
requests before it closes the database."""

import asyncio

import pytest
from fastapi import routing
from fastapi.testclient import TestClient

from app import main
from app.waits import beside


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
        _ = beside(works_until_cancelled(), name="work until shutdown")

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
