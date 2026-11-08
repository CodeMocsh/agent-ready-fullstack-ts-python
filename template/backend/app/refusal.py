"""How a route says no: `ApiError`, which a refusal subclasses to carry its own status and
sentence, and `responses(...)`, which builds a route's declaration from the same classes.

The template owns this module and updates it. The refusals themselves are the project's, in
`app/errors.py`. `tests/errors/test_errors.py` fails when a route raises one it does not
declare. `docs/adr/template/0006` and `docs/adr/template/0015`.

A misconfigured deployment raises from `app/wiring.py` and stops the process. A request with no
principal raises `Unauthenticated` from `app/identity/shared.py`, which `docs/adr/template/0004`
keeps out of the spec.
"""

from typing import Any, ClassVar, Final, Literal, final

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

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
class ResponseTooLarge(ApiError):
    """A finite response exceeded its declared byte limit."""

    status = 500
    description = "Response exceeds its byte limit"


@final
class ResponseTimedOut(ApiError):
    """A finite response did not complete its body within the response budget."""

    status = 500
    description = "Response did not finish within its time limit"


async def internal_error(_request: Request, _error: Exception) -> JSONResponse:
    """An unhandled HTTP failure, with the error shape the API declares."""
    return JSONResponse(
        status_code=500,
        content=ErrorBody(detail="Internal server error").model_dump(),
        headers={"cache-control": "no-store"},
    )


@final
class NoSuchAsset(ApiError):
    """A hashed file the bundle does not hold, asked of `app.serve`. Outside the contract: the
    route that raises it is not in `openapi.json`."""

    status = 404
    description = "No such bundled asset"


@final
class NoSuchJob(ApiError):
    """A job the tenant has no such job of. `GET /jobs/{id}` declares it."""

    status = 404
    description = "Job not found"


@final
class UnstorableInput(ApiError):
    """A route was handed a value that the job it asks for cannot hold: a number that is not
    finite, the character U+0000, or a surrogate code point."""

    status = 422
    description = "The input holds a value that cannot be stored"


@final
class NoConnectionFree(ApiError):
    """A request that the database did not serve: no connection came in time, a new one did not
    open, or its connection ended. `app/main.py` answers it with a `Retry-After`, and every tenant
    route declares it. `docs/adr/template/0022`."""

    status = 503
    description = "No database connection is free. Ask again after the seconds in Retry-After"


def no_connection_free(retry_after: int) -> JSONResponse:
    """The `503` that `NoConnectionFree` declares, asking the client to wait `retry_after`
    seconds."""
    return JSONResponse(
        status_code=NoConnectionFree.status,
        content=ErrorBody(detail=NoConnectionFree.description).model_dump(),
        headers={"Retry-After": str(retry_after)},
    )


@final
class GroupTooLarge(ApiError):
    """A `GET /reads` that names more paths than its response can name, each as a part that left.
    `docs/adr/template/0029`."""

    status = 422
    description = "The group names more paths than its response can hold"


Bound = Literal["in_flight", "rate", "streams"]
"""Which bound of `app/admission.py` refused a request."""

SAID_OF_EACH_BOUND: Final[dict[Bound, str]] = {
    "in_flight": "too many requests at once",
    "rate": "too many requests in a short time",
    "streams": "too many streams open",
}


@final
class TooManyRequests(ApiError):
    """A request over what this process admits for one actor. `app/admission.py` raises it with a
    `Retry-After`, and every tenant route declares it. `docs/adr/template/0023`."""

    status = 429
    description = "Too many requests from this actor. Ask again after the seconds in Retry-After"

    def __init__(self, *, retry_after: int, bound: Bound) -> None:
        super().__init__(
            f"Too many requests from this actor: {SAID_OF_EACH_BOUND[bound]}. Ask again after the "
            f"seconds in Retry-After"
        )
        self.headers = {"Retry-After": str(retry_after)}
        self.bound: Bound = bound


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
