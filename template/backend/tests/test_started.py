"""Outside `tests/started/` and the tiers, a test that starts the app fails, and the failure says
where the test belongs. Building the app is not starting it. The check is made when the lifespan
starts, wherever the app was built. A started app lives for one test."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import lifespan
from app.main import create_app
from tests.conftest import (
    STARTED,
    TESTS,
    StartedForMoreThanOneTest,
    StartedOutsideItsFolder,
    may_start,
    stopped_by_the_test_that_started_it,
)
from tests.tiers import python_tiers

BUILT_AT_IMPORT = FastAPI(lifespan=lifespan.lifespan)


def test_a_test_that_starts_the_app_here_fails_and_names_where_it_belongs() -> None:
    with (
        pytest.raises(StartedOutsideItsFolder, match=r"test_started\.py.*tests\.routed\.routed"),
        TestClient(create_app()),
    ):
        pass


def test_an_app_built_at_import_is_refused_when_a_test_here_starts_it() -> None:
    with pytest.raises(StartedOutsideItsFolder), TestClient(BUILT_AT_IMPORT):
        pass


@pytest.fixture(scope="module")
def started_for_the_module() -> Iterator[StartedOutsideItsFolder]:
    with (
        pytest.raises(StartedOutsideItsFolder) as refused,
        TestClient(FastAPI(lifespan=lifespan.lifespan)),
    ):
        pass
    yield refused.value


def test_a_fixture_wider_than_one_test_is_refused_when_it_starts_the_app(
    started_for_the_module: StartedOutsideItsFolder,
) -> None:
    assert "test_started.py" in str(started_for_the_module)


def test_a_test_that_builds_the_app_here_may_drive_it_without_starting_it() -> None:
    assert TestClient(create_app()).get("/health").json() == {"status": "ok"}


def test_a_test_in_started_or_in_a_tier_may_start_the_app() -> None:
    assert may_start(TESTS / STARTED / "test_a.py")
    for tier in python_tiers():
        assert may_start(TESTS / tier.folder / "test_a.py")
    assert not may_start(TESTS / "routes" / STARTED / "test_a.py")


def test_a_started_app_that_another_test_stops_is_refused(request: pytest.FixtureRequest) -> None:
    stopped_by_the_test_that_started_it(request.node, request.node)

    with pytest.raises(StartedForMoreThanOneTest, match="lives for one test"):
        stopped_by_the_test_that_started_it(request.node, None)
