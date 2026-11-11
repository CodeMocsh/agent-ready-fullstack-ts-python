from typing import Final

from pydantic import BaseModel, Field

from app.models.shared import ID_MAX, PAGE_MAX

TITLE_MAX: Final = 500
"""The most characters a task's title holds. Four bytes each fit the bound on `tasks.title`."""


class Task(BaseModel):
    """A title the API took holds `TITLE_MAX` characters. A title that another writer stored holds
    at most what `tasks.title` holds, four bytes for each of those characters, so at most that
    many characters."""

    id: str = Field(max_length=ID_MAX)
    title: str = Field(max_length=4 * TITLE_MAX)
    done: bool


class TaskPage(BaseModel):
    tasks: list[Task] = Field(max_length=PAGE_MAX)
    next: str | None = Field(max_length=ID_MAX)


class CreateTaskBody(BaseModel):
    title: str = Field(max_length=TITLE_MAX)


class UpdateTaskBody(BaseModel):
    done: bool
