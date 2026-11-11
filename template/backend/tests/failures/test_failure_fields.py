"""A response field named for a failure is a `Failure`, never free text.

Read from the contract the app serves, not from `openapi.json`, so it holds without
`make openapi`. `STILL_FREE_TEXT` in `app/failures.py` lists each field that is free text today.
`docs/adr/template/0031`.
"""

import re
from collections.abc import Iterator
from typing import Any, Final

from fastapi import FastAPI
from pydantic import BaseModel

from app.failures import STILL_FREE_TEXT
from app.main import create_app
from app.models import Failure

FAILURE: Final = {"$ref": "#/components/schemas/Failure"}

NULL: Final = {"type": "null"}

NAMED_FOR_A_FAILURE: Final = re.compile(r"(^|_)(errors?|failures?)$")

SAYS_NOTHING: Final = frozenset({"title", "description", "default", "readOnly"})
"""The keys of a property that do not change what it holds."""


def _refs(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value.rsplit("/", 1)[1]
            else:
                yield from _refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from _refs(value)


def _answered(spec: dict[str, Any]) -> dict[str, Any]:
    """Each schema that a `2xx` response reaches, by name."""
    schemas = spec["components"]["schemas"]
    pending = [
        name
        for operations in spec["paths"].values()
        for operation in operations.values()
        for status, response in operation["responses"].items()
        if status.startswith("2")
        for name in _refs(response.get("content", {}))
    ]
    reached: dict[str, Any] = {}
    while pending:
        name = pending.pop()
        if name not in reached:
            reached[name] = schemas[name]
            pending.extend(_refs(schemas[name]))
    return reached


def _holds_a_failure(schema: dict[str, Any]) -> bool:
    shape = {key: value for key, value in schema.items() if key not in SAYS_NOTHING}
    if shape == FAILURE:
        return True
    if shape.get("type") == "array" and set(shape) == {"type", "items"}:
        return _holds_a_failure(shape["items"])
    if set(shape) == {"anyOf"}:
        held = [one for one in shape["anyOf"] if one != NULL]
        return len(held) == 1 and _holds_a_failure(held[0])
    return False


def free_text(spec: dict[str, Any]) -> set[str]:
    """Each `Schema.property` that a `2xx` response reaches, named for a failure, that does not
    hold a `Failure`."""
    return {
        f"{name}.{field}"
        for name, schema in _answered(spec).items()
        for field, held in schema.get("properties", {}).items()
        if NAMED_FOR_A_FAILURE.search(field) and not _holds_a_failure(held)
    }


def test_every_failure_a_response_carries_is_a_failure() -> None:
    free = sorted(free_text(create_app().openapi()) - STILL_FREE_TEXT)

    assert free == [], (
        f"{free} carry a failure as free text, which shows a person an error's own words. Make "
        f"each a Failure, and raise a failure declared in app/failures.py where the work fails."
    )


def test_every_field_still_free_text_is_listed_and_still_free_text() -> None:
    gone = sorted(STILL_FREE_TEXT - free_text(create_app().openapi()))

    assert gone == [], (
        f"{gone} are in STILL_FREE_TEXT in app/failures.py and no longer carry free text. "
        f"Remove each from the list."
    )


class _Track(BaseModel):
    error: str
    last_failure: Failure | None
    failures: list[Failure]
    step_errors: list[str]
    reason: str


class _Asked(BaseModel):
    failure: str


def test_the_check_names_each_free_text_failure_and_passes_each_failure() -> None:
    """Request bodies are out of scope: a client names its own failures, as `ClientEvent` does."""
    app = FastAPI(separate_input_output_schemas=False)

    @app.post("/tracks")
    def track(asked: _Asked) -> _Track:
        raise NotImplementedError(asked)

    assert free_text(app.openapi()) == {"_Track.error", "_Track.step_errors"}
