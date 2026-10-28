"""The lines the lifespan writes before the first request, in the format of every other line."""

import pytest
from fastapi.testclient import TestClient

from app.deployment import RESOURCE_ATTRIBUTES_ENV
from app.main import create_app
from tests.conftest import Logged


def test_the_boot_lines_are_in_the_same_format(logged: Logged) -> None:
    """The lines a deployment greps first -- which substrate, whether it authenticates -- are
    written before any request, so they are the ones a request-only test would miss."""
    with TestClient(create_app()):
        pass

    said = {line["message"]: line for line in logged()}

    assert said["serving"]["substrate"] == "memory"
    assert said["serving"]["severity"] == "INFO"


def test_the_boot_lines_name_the_version_the_deployment_gave(
    logged: Logged, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(RESOURCE_ATTRIBUTES_ENV, "service.version=1.4.2")

    with TestClient(create_app()):
        pass

    lines = logged()

    assert "serving" in {line["message"] for line in lines}
    assert {line["service.version"] for line in lines} == {"1.4.2"}
