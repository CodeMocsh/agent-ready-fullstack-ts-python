"""A failure is declared once, in `app/failures.py`, and a person reads only its cause and its
sentence. `docs/adr/template/0031`."""

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app import failing, failures
from app.failing import AttemptsSpent, Failing, Internal, UndeclarableFailure, cause_of
from app.models import Failure

SOURCE = Path(__file__).resolve().parents[2] / "app"

LIST = SOURCE / "failures.py"


def _declared_in_app_failures() -> list[str]:
    return sorted(
        name
        for name, value in vars(failures).items()
        if isinstance(value, type) and issubclass(value, Failing) and value is not Failing
    )


def _named_outside_app_failures() -> set[str]:
    named: set[str] = set()
    for path in sorted(SOURCE.rglob("*.py")):
        if path == LIST or "__pycache__" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Name):
                named.add(node.id)
            elif isinstance(node, ast.Attribute):
                named.add(node.attr)
    return named


def test_an_error_that_declares_no_cause_is_internal() -> None:
    assert cause_of(ValueError("node 'review' had 1 child run not finish")) == Internal.cause


def test_a_declared_failure_names_its_own_cause() -> None:
    assert cause_of(AttemptsSpent()) == AttemptsSpent.cause


def test_a_failure_says_only_the_sentence_its_cause_declares() -> None:
    read = Failure.model_validate({"cause": "internal", "said": "an engine's own words"})

    assert read.model_dump() == {"cause": "internal", "said": Internal.said}


def test_reading_a_cause_that_no_class_declares_raises() -> None:
    with pytest.raises(ValidationError, match="'a_retired_cause' is not a declared cause"):
        _ = Failure(cause="a_retired_cause")


def test_a_failure_declared_outside_app_failures_is_refused() -> None:
    with pytest.raises(UndeclarableFailure, match="app/failures.py"):
        _ = type("Elsewhere", (Failing,), {"cause": "elsewhere", "said": "It failed elsewhere."})


@pytest.mark.parametrize(
    ("cause", "said", "refused"),
    [
        ("internal", "It failed again.", "which is declared"),
        ("NodeFailed", "It failed.", "not a snake_case name"),
        ("unsaid", "", "not a sentence"),
    ],
)
def test_a_failure_that_cannot_be_read_as_a_cause_is_refused(
    cause: str, said: str, refused: str
) -> None:
    body = {"__module__": failures.__name__, "cause": cause, "said": said}

    with pytest.raises(UndeclarableFailure, match=refused):
        _ = type("Refused", (Failing,), body)


def test_every_failure_in_app_failures_is_built_on_app_failing() -> None:
    """`app/failures.py` is the project's and an update never touches it, so a failure it builds
    on its own base class passes every test above by being invisible to them."""
    own = sorted(
        name
        for name, value in vars(failures).items()
        if isinstance(value, type)
        and issubclass(value, Exception)
        and not issubclass(value, failing.Failing)
    )

    assert own == [], (
        f"{own} in app/failures.py are not built on app.failing.Failing. Subclass Failing, "
        f"which app/failures.py re-exports."
    )


def test_every_failure_in_app_failures_is_raised_somewhere() -> None:
    unraised = sorted(set(_declared_in_app_failures()) - _named_outside_app_failures())

    assert unraised == [], (
        f"{unraised} are declared in app/failures.py and named nowhere else in app/. Raise each "
        f"where the work fails, or delete it."
    )
