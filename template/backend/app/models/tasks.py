from typing import Final

from pydantic import BaseModel, Field

PAGE_MAX: Final = 100
"""The most rows one page of a list holds. `docs/adr/template/0026`."""


class Task(BaseModel):
    id: str
    title: str
    done: bool


class TaskPage(BaseModel):
    tasks: list[Task] = Field(max_length=PAGE_MAX)
    next: str | None


class CreateTaskBody(BaseModel):
    title: str


class UpdateTaskBody(BaseModel):
    done: bool
