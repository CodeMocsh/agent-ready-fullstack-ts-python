"""Which environment a process is, and that production refuses every finding at once."""

import pytest

from app import deployment, environment
from app.deployment import (
    DEVELOPMENT,
    ENVIRONMENT_ENV,
    DevelopmentSettingInProduction,
    UnknownEnvironment,
    in_development,
    refuse_in_production,
)


@pytest.fixture
def unnamed(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.delenv(ENVIRONMENT_ENV)
    return monkeypatch


@pytest.mark.usefixtures("unnamed")
def test_unset_is_production() -> None:
    assert not in_development()


@pytest.mark.parametrize("named", ["production", " production "])
def test_production_named_is_production(unnamed: pytest.MonkeyPatch, named: str) -> None:
    unnamed.setenv(ENVIRONMENT_ENV, named)

    assert not in_development()


@pytest.mark.parametrize("named", ["prod", "dev", "Development", "staging"])
def test_an_environment_that_is_neither_is_refused(unnamed: pytest.MonkeyPatch, named: str) -> None:
    unnamed.setenv(ENVIRONMENT_ENV, named)

    with pytest.raises(UnknownEnvironment, match=repr(named)):
        in_development()


@pytest.mark.usefixtures("unnamed")
def test_production_names_every_finding_at_once() -> None:
    with pytest.raises(DevelopmentSettingInProduction) as refused:
        refuse_in_production(["the first", "the second"])

    assert "the first" in str(refused.value)
    assert "the second" in str(refused.value)


@pytest.mark.usefixtures("unnamed")
def test_production_with_nothing_found_is_served() -> None:
    refuse_in_production([])


def test_the_development_loop_may_carry_anything(unnamed: pytest.MonkeyPatch) -> None:
    unnamed.setenv(ENVIRONMENT_ENV, DEVELOPMENT)

    refuse_in_production(["anything"])


MECHANISM = (
    "in_development",
    "stated",
    "refuse_in_production",
    "UnknownEnvironment",
    "DevelopmentSettingInProduction",
)


def test_app_environment_defines_none_of_the_mechanism_itself() -> None:
    """`app/environment.py` is the project's and an update never touches it, so a project
    generated before `app/deployment.py` existed still defines its own `in_development` there,
    and stops receiving the template's changes to it."""
    own = [
        name
        for name in MECHANISM
        if getattr(environment, name, getattr(deployment, name)) is not getattr(deployment, name)
    ]

    assert own == [], (
        f"app/environment.py defines {own} itself. Delete them, import them from app.deployment, "
        f"and keep only what this project's production refuses."
    )
