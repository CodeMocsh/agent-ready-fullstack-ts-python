"""Outside `tests/started/` and the tiers, a test that starts the app fails and says where it
belongs. Building the app is not starting it."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import StartedOutsideItsFolder


def test_a_test_that_starts_the_app_here_fails_and_names_where_it_belongs() -> None:
    with (
        pytest.raises(StartedOutsideItsFolder, match=r"test_started\.py.*tests\.routed\.routed"),
        TestClient(create_app()),
    ):
        pass


def test_a_test_that_builds_the_app_here_may_drive_it_without_starting_it() -> None:
    assert TestClient(create_app()).get("/health").json() == {"status": "ok"}
