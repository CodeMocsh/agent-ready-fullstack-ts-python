"""`/health` says the process answers; `/ready` says it can serve."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as fresh:
        yield fresh


def test_a_process_whose_substrate_answers_is_ready(client: TestClient) -> None:
    answered = client.get("/ready")

    assert answered.status_code == 200
    assert answered.json() == {"status": "ready"}


def test_a_process_whose_substrate_fails_its_check_is_not_ready(
    app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unreachable() -> str:
        raise ConnectionRefusedError("the substrate does not answer")

    monkeypatch.setattr(app.state.database, "check", unreachable)

    assert client.get("/ready").status_code == 500
    assert client.get("/health").status_code == 200
