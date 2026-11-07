from fastapi import APIRouter, Response

from app.deps import JobsDep, StoreDep
from app.errors import NoSuchTask, responses
from app.jobs import CLOSE_A_TASK
from app.models import CreateTaskBody, Job, Task, TaskPage, UpdateTaskBody

router = APIRouter()


@router.get("/tasks", responses=responses(NoSuchTask))
async def list_tasks(store: StoreDep, after: str | None = None) -> TaskPage:
    page = await store.list(after)
    if page is None:
        raise NoSuchTask
    return page


@router.post("/tasks", status_code=201)
async def create_task(store: StoreDep, body: CreateTaskBody) -> Task:
    return await store.create(body)


@router.patch("/tasks/{id}", responses=responses(NoSuchTask))
async def update_task(store: StoreDep, id: str, body: UpdateTaskBody) -> Task:
    updated = await store.update(id, body)
    if updated is None:
        raise NoSuchTask
    return updated


@router.delete("/tasks/{id}", status_code=204, responses=responses(NoSuchTask))
async def delete_task(store: StoreDep, id: str) -> Response:
    if not await store.remove(id):
        raise NoSuchTask
    return Response(status_code=204)


@router.post("/tasks/{id}/close", status_code=202)
async def close_task(jobs: JobsDep, id: str) -> Job:
    return await jobs.ask(CLOSE_A_TASK, {"id": id})
