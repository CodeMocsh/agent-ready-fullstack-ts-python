"""The one-origin server runs the lifespan of the app it mounts."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.serve import INDEX, create_server


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """The in-memory substrate, which is what no `DATABASE_URL` means, behind a bundle that holds
    only its shell."""
    (tmp_path / INDEX).write_text("<!doctype html>")
    with TestClient(create_server(tmp_path, create_app())) as fresh:
        yield fresh


def test_the_mounted_api_gets_its_lifespan(client: TestClient) -> None:
    """The regression this module exists for.

    Starlette does not run a mounted application's lifespan, so without the delegation nothing
    reaches `app.state` and this answers 500. `/ready` is asked because it needs the database
    to answer at all — a route that only read its own arguments would pass either way.
    """
    answered = client.get("/api/ready")
    assert answered.status_code == 200, answered.text
    assert answered.json() == {"status": "ready"}
