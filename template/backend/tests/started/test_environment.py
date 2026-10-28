"""A production process that carries a development setting does not start."""

import pytest
from fastapi.testclient import TestClient

from app.deployment import DATABASE_URL_ENV, ENVIRONMENT_ENV, DevelopmentSettingInProduction
from app.main import create_app


def test_a_production_process_on_memory_does_not_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refused by the lifespan, so the process that cannot serve never answers a request."""
    monkeypatch.delenv(ENVIRONMENT_ENV)
    monkeypatch.delenv(DATABASE_URL_ENV, raising=False)

    with pytest.raises(DevelopmentSettingInProduction), TestClient(create_app()):
        pass
