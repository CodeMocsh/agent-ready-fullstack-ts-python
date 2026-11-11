from typing import Final

from pydantic import BaseModel, Field

from app.models.shared import PAGE_MAX

TITLE_MAX: Final = 500
"""The most characters a task's title holds. Four bytes each fit the bound on `tasks.title`."""


class Task(BaseModel):
    id: str
    title: str
    done: bool


class TaskPage(BaseModel):
    tasks: list[Task] = Field(max_length=PAGE_MAX)
    next: str | None


class CreateTaskBody(BaseModel):
    title: str = Field(max_length=TITLE_MAX)


class UpdateTaskBody(BaseModel):
    done: bool
