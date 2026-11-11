"""How a failure reaches a person: `Failing`, which a declared failure subclasses to carry its own
cause and sentence, and `cause_of`, which names the cause of any error.

The template owns this module and updates it. The project's failures are in `app/failures.py`.
A row stores the cause, and a response carries it as a `Failure` with its sentence. An error's
message goes to the log and never to a row. `docs/adr/template/0031` and
`docs/adr/template/0015`.
"""

import re
from importlib import import_module
from typing import ClassVar, Final, final

DECLARED_IN: Final = ("app.failing", "app.failures")
"""The only modules that may declare a failure."""

CAUSE: Final = re.compile(r"^[a-z][a-z0-9_]*$")
"""How a cause is spelled. An error class is named in CapWords, so a stored class name never reads
as a cause."""

_DECLARED: dict[str, str] = {}


class UndeclarableFailure(TypeError):
    """A `Failing` subclass that cannot be a declaration: declared outside `DECLARED_IN`, with a
    cause spelled outside `CAUSE` or already declared, or with no sentence."""


class Failing(Exception):
    """One cause of a failure that a person reads. Raise a subclass where the work fails.

    `cause` is what a row stores and the contract names. `said` says what happened in plain
    words, and names no part of the system, no count and no instruction. Declaring a subclass
    outside `DECLARED_IN`, or with a cause that another class declares, raises
    `UndeclarableFailure`.
    """

    cause: ClassVar[str]
    said: ClassVar[str]

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        if cls.__module__ not in DECLARED_IN:
            raise UndeclarableFailure(
                f"{cls.__module__}.{cls.__name__} declares a failure. Declare it in "
                f"app/failures.py, so the contract names its cause."
            )
        cause, said = cls.__dict__.get("cause"), cls.__dict__.get("said")
        if not isinstance(cause, str) or CAUSE.fullmatch(cause) is None:
            raise UndeclarableFailure(f"{cls.__name__}.cause is {cause!r}, not a snake_case name")
        if not isinstance(said, str) or not said:
            raise UndeclarableFailure(f"{cls.__name__}.said is {said!r}, not a sentence")
        if cause in _DECLARED:
            raise UndeclarableFailure(f"{cls.__name__} declares {cause!r}, which is declared")
        _DECLARED[cause] = said


@final
class Internal(Failing):
    """Any error that is not a `Failing`. `cause_of` names it, and nothing raises it."""

    cause = "internal"
    said = "Something went wrong in this service."


@final
class AttemptsSpent(Failing):
    """A job whose last attempt ended with no outcome and no error: its lease ended, or its
    worker stopped."""

    cause = "attempts_spent"
    said = "The work stopped before it could finish."


def cause_of(error: BaseException) -> str:
    """The cause `error` declares, or `Internal`'s for an error that declares none."""
    return error.cause if isinstance(error, Failing) else Internal.cause


def declared() -> dict[str, str]:
    """Each cause declared in `DECLARED_IN`, and its sentence."""
    for module in DECLARED_IN:
        _ = import_module(module)
    return dict(_DECLARED)
