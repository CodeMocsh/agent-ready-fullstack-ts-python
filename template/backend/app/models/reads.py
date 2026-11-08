"""The answer of `GET /reads`: one entry for each part, in the order the parts were asked."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

LeftBecause = Literal["timed out", "not a part", "past the cap", "too large"]
"""Why a part left its group. The client sends a part that left again, alone."""


class AnsweredPart(BaseModel):
    kind: Literal["answered"]
    status: int
    headers: dict[str, str]
    body: str


class LeftPart(BaseModel):
    kind: Literal["left"]
    reason: LeftBecause


class Reads(BaseModel):
    parts: list[Annotated[AnsweredPart | LeftPart, Field(discriminator="kind")]]
