"""The lines the lifespan writes before the first request, in the format of every other line."""

from fastapi.testclient import TestClient

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
