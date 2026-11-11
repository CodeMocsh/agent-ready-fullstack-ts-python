"""A failure as a person reads it: its declared cause, and the sentence that says what happened.

A field named `error` or `failure`, or ending in `_error` or `_failure`, in a response is a
`Failure`. `tests/failures/test_failure_fields.py` holds every response to that.
`docs/adr/template/0031`.
"""

from typing import Annotated, ClassVar

from pydantic import AfterValidator, BaseModel, ConfigDict, WithJsonSchema, computed_field

from app.failing import declared

SAID = declared()
"""Each declared cause, and its sentence."""


def _declared(cause: str) -> str:
    if cause not in SAID:
        raise ValueError(f"{cause!r} is not a declared cause")
    return cause


Cause = Annotated[
    str, AfterValidator(_declared), WithJsonSchema({"type": "string", "enum": list(SAID)})
]
"""A cause that `app/failing.py` or `app/failures.py` declares. Any other name fails validation."""


class Failure(BaseModel):
    """Why some work failed. It holds no text but the sentence its cause declares."""

    model_config: ClassVar[ConfigDict] = ConfigDict(json_schema_mode_override="serialization")

    cause: Cause

    @computed_field
    @property
    def said(self) -> str:
        return SAID[self.cause]
