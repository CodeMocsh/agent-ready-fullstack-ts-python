from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response

from app.deps import StoreDep
from app.identity import tenant_for
from app.models import CreateTaskBody, ErrorBody, Task, UpdateTaskBody

router = APIRouter(dependencies=[Depends(tenant_for)])
"""Every route that reads or writes what belongs to a tenant.

The tenant is a property of the router, not of what a handler injects, so a route declared here
resolves one before any handler runs -- whether or not it asks for a store. `docs/adr/0008` says why that
is not the obvious arrangement, and `tests/routes/test_guarantee.py` is what holds it.
"""


public_router = APIRouter()
"""The routes that may answer without resolving a tenant.

One today, and which ones is named in `tests/routes/test_guarantee.py` rather than marked here.
`docs/adr/0008` says why an exemption is a list and never a decorator.
"""


@public_router.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    """That this process is answering, for whatever decides whether to route to it.

    Liveness rather than readiness: the lifespan verifies the substrate and refuses to start
    without it. Out of the schema because the frontend never calls it, and `openapi.json`
    describes the API rather than the infrastructure around it.
    """
    return {"status": "ok"}


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
