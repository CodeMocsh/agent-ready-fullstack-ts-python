from pydantic import BaseModel


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
