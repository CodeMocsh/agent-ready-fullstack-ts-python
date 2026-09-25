from typing import Annotated, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field


class Task(BaseModel):
    id: str
    title: str
    done: bool


class CreateTaskBody(BaseModel):
    title: str


class UpdateTaskBody(BaseModel):
    done: bool


class ErrorBody(BaseModel):
    detail: str


MAX_CLIENT_EVENTS: Final = 20

RouteId = Annotated[str, Field(max_length=200, pattern=r"^[A-Za-z0-9_$./-]+$")]
ErrorName = Annotated[str, Field(max_length=100, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")]
RequestId = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
Status = Annotated[int, Field(ge=100, le=599)]


class ClientEvent(BaseModel):
    """One failure the browser saw, named rather than described. No field takes free text:
    the route is the router's template and the error is its class name."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    kind: Literal["uncaught", "caught", "query", "mutation"]
    route: RouteId | None
    error: ErrorName
    status: Status | None
    request_id: RequestId | None


class ClientEvents(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    events: Annotated[list[ClientEvent], Field(min_length=1, max_length=MAX_CLIENT_EVENTS)]
