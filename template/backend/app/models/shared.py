"""The shapes every other module here may build on, and which build on nothing."""

from pydantic import BaseModel


class ErrorBody(BaseModel):
    detail: str
