from typing import Any

from fastapi import APIRouter, HTTPException, Response

from app.deps import StoreDep
from app.models import CreateTaskBody, ErrorBody, Task, UpdateTaskBody

router = APIRouter()

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"description": "Task not found", "model": ErrorBody}
}


@router.get("/tasks")
async def list_tasks(store: StoreDep) -> list[Task]:
    return await store.list()


@router.post("/tasks", status_code=201)
async def create_task(store: StoreDep, body: CreateTaskBody) -> Task:
    return await store.create(body)


@router.patch("/tasks/{id}", responses=NOT_FOUND)
async def update_task(store: StoreDep, id: str, body: UpdateTaskBody) -> Task:
    updated = await store.update(id, body)
    if updated is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return updated


@router.delete("/tasks/{id}", status_code=204, responses=NOT_FOUND)
async def delete_task(store: StoreDep, id: str) -> Response:
    if not await store.remove(id):
        raise HTTPException(status_code=404, detail="Task not found")
    return Response(status_code=204)
