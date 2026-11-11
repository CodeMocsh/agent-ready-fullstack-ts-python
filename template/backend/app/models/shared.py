"""What every other module here may build on, and which builds on nothing."""

from typing import Final

from pydantic import BaseModel

PAGE_MAX: Final = 100
"""The most rows one page of a list holds. `docs/adr/template/0026`."""

PAGE_BYTES: Final = 1024 * 1024
"""The most bytes one page of a list holds, and the most the bounds on one row's columns may
add up to. `docs/adr/template/0026`."""


ID_MAX: Final = 36
"""The most characters an id holds: a uuid as text."""

NAME_MAX: Final = 1024
"""The most characters a name holds, such as the kind of a job or the failure of one."""

MAX_BYTES: Final = "x-max-bytes"
"""The key that gives a part of a schema the most bytes its value takes as JSON, where the schema
alone cannot: a value that is any JSON. The code that writes the value holds it to that bound.
`docs/adr/template/0028`."""


class ErrorBody(BaseModel):
    detail: str
