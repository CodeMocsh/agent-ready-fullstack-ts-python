"""Every way this service says no, once each: a class carrying its own status and sentence.

A route raises the class and names it in `responses(...)` on its decorator.
`tests/errors/test_errors.py` fails when the two disagree. `docs/adr/0012`.

Only what a caller can provoke belongs here. A misconfigured deployment raises from
`app/wiring.py` and stops the process. A request with no tenant raises `Unauthenticated` from
`app/identity.py`, which `docs/adr/0008` keeps out of the spec.
"""

from typing import Any, ClassVar, final

from fastapi import HTTPException

from app.models import ErrorBody


class ApiError(HTTPException):
    """One way a route answers no.

    `description` is what the contract says about the status. The body's `detail` is the
    description, unless the raise passes one that says more about this occurrence.
    """

    status: ClassVar[int]
    description: ClassVar[str]

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(
            status_code=self.status, detail=self.description if detail is None else detail
        )


@final
class NoSuchTask(ApiError):
    status = 404
    description = "Task not found"


@final
class NoSuchAsset(ApiError):
    """A hashed file the bundle does not hold, asked of `app.serve`. Outside the contract: the
    route that raises it is not in `openapi.json`."""

    status = 404
    description = "No such bundled asset"


def _joined(descriptions: list[str]) -> str:
    """The descriptions of refusals that share a status, as one sentence joined by ", or"."""
    first, *rest = descriptions
    return ", or ".join([first, *(one[:1].lower() + one[1:] for one in rest)])


def responses(*errors: type[ApiError]) -> dict[int | str, dict[str, Any]]:
    """The `responses=` declaration for a route that can raise `errors`.

    One entry per status, each carrying `ErrorBody` as its model, so the spec describes the body
    `HTTPException` sends.
    """
    by_status: dict[int, list[str]] = {}
    for error in errors:
        by_status.setdefault(error.status, []).append(error.description)
    return {
        status: {"description": _joined(descriptions), "model": ErrorBody}
        for status, descriptions in by_status.items()
    }
