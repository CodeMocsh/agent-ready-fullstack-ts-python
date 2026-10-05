"""What production refuses to carry, and that the development loop may carry it.

An unset `APP_ENV` is production, so every test here says which environment it means rather
than inheriting the `development` that `tests/conftest.py` gives the rest of the suite.
"""

import pytest
from fastapi.testclient import TestClient

from app.environment import (
    DATABASE_URL_ENV,
    ENVIRONMENT_ENV,
    DevelopmentSettingInProduction,
    UnknownEnvironment,
    refuse_development_settings,
)
from app.main import create_app


@pytest.fixture
def unnamed(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.delenv(ENVIRONMENT_ENV)
    return monkeypatch


def test_a_deployment_with_a_database_is_served(unnamed: pytest.MonkeyPatch) -> None:
    unnamed.setenv(DATABASE_URL_ENV, "postgres://app_app@db.internal:5432/app")

    refuse_development_settings()


def test_production_refuses_the_in_memory_substrate(unnamed: pytest.MonkeyPatch) -> None:
    unnamed.delenv(DATABASE_URL_ENV, raising=False)

    with pytest.raises(DevelopmentSettingInProduction, match=DATABASE_URL_ENV):
        refuse_development_settings()


@pytest.mark.parametrize("named", ["production", " production "])
def test_production_named_is_production(unnamed: pytest.MonkeyPatch, named: str) -> None:
    unnamed.setenv(ENVIRONMENT_ENV, named)
    unnamed.setenv(DATABASE_URL_ENV, "  ")

    with pytest.raises(DevelopmentSettingInProduction):
        refuse_development_settings()


def test_the_development_loop_may_run_on_memory(unnamed: pytest.MonkeyPatch) -> None:
    unnamed.setenv(ENVIRONMENT_ENV, "development")
    unnamed.delenv(DATABASE_URL_ENV, raising=False)

    refuse_development_settings()


@pytest.mark.parametrize("named", ["prod", "dev", "Development", "staging"])
def test_an_environment_that_is_neither_is_refused(unnamed: pytest.MonkeyPatch, named: str) -> None:
    unnamed.setenv(ENVIRONMENT_ENV, named)

    with pytest.raises(UnknownEnvironment, match=repr(named)):
        refuse_development_settings()


def test_a_production_process_on_memory_does_not_start(unnamed: pytest.MonkeyPatch) -> None:
    """Refused by the lifespan, so the process that cannot serve never answers a request."""
    unnamed.delenv(DATABASE_URL_ENV, raising=False)

    with pytest.raises(DevelopmentSettingInProduction), TestClient(create_app()):
        pass
