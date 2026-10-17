from fastapi import APIRouter, Response

from app.deps import PrincipalDep, StoreDep
from app.errors import NoSuchTask, responses
from app.models import CreateTaskBody, Task, UpdateTaskBody

router = APIRouter()


@router.get("/tasks")
async def list_tasks(store: StoreDep, principal: PrincipalDep) -> list[Task]:
    return await store.list(principal.actor)


@router.post("/tasks", status_code=201)
async def create_task(store: StoreDep, principal: PrincipalDep, body: CreateTaskBody) -> Task:
    return await store.create(principal.actor, body)


@router.patch("/tasks/{id}", responses=responses(NoSuchTask))
async def update_task(
    store: StoreDep, principal: PrincipalDep, id: str, body: UpdateTaskBody
) -> Task:
    updated = await store.update(principal.actor, id, body)
    if updated is None:
        raise NoSuchTask
    return updated


@router.delete("/tasks/{id}", status_code=204, responses=responses(NoSuchTask))
async def delete_task(store: StoreDep, principal: PrincipalDep, id: str) -> Response:
    if not await store.remove(principal.actor, id):
        raise NoSuchTask
    return Response(status_code=204)
