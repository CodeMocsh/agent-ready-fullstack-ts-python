from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import ROUTERS
from app.store import Database
from tests.conftest import Logged
from tests.routed import assembled
from tests.widgets import with_widgets


@pytest.fixture
def app(database: Database) -> FastAPI:
    return with_widgets(assembled(*ROUTERS, database=database))


@pytest.fixture
def client(app: FastAPI, logged: Logged) -> Iterator[TestClient]:
    """A client onto `app`, with what came before its first request already read, so a test sees
    only its own requests."""
    with TestClient(app) as fresh:
        logged()
        yield fresh
