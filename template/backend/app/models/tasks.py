from pydantic import BaseModel, Field

from app.models.shared import PAGE_MAX


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
