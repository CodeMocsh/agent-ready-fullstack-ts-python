from typing import Any

from fastapi import APIRouter, HTTPException, Response

from app.models import CreateTaskBody, ErrorBody, Task, UpdateTaskBody
from app.store import task_store

router = APIRouter()

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"description": "Task not found", "model": ErrorBody}
}


@router.get("/tasks")
def list_tasks() -> list[Task]:
    return task_store.list()


@router.post("/tasks", status_code=201)
def create_task(body: CreateTaskBody) -> Task:
    return task_store.create(body)


@router.patch("/tasks/{id}", responses=NOT_FOUND)
def update_task(id: str, body: UpdateTaskBody) -> Task:
    updated = task_store.update(id, body)
    if updated is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return updated


@router.delete("/tasks/{id}", status_code=204, responses=NOT_FOUND)
def delete_task(id: str) -> Response:
    if not task_store.remove(id):
        raise HTTPException(status_code=404, detail="Task not found")
    return Response(status_code=204)
