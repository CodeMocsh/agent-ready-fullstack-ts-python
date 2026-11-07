"""What every other module here may build on, and which builds on nothing."""

from typing import Final

from pydantic import BaseModel

PAGE_MAX: Final = 100
"""The most rows one page of a list holds. `docs/adr/template/0026`."""


class ErrorBody(BaseModel):
    detail: str
