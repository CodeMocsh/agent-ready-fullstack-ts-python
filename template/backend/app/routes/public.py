"""The routes that may answer without resolving a tenant.

Which ones is named in `tests/routes/test_guarantee.py` rather than marked here.
`docs/adr/template/0008` says why an exemption is a list and never a decorator.
"""

from fastapi import APIRouter, Request, Response

from app import log
from app.deps import database_of
from app.models import ClientEvents

router = APIRouter()


@router.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    """That this process is answering, for whatever decides whether to route to it.

    Liveness rather than readiness: the lifespan verifies the substrate and refuses to start
    without it. Out of the schema because the frontend never calls it, and `openapi.json`
    describes the API rather than the infrastructure around it.
    """
    return {"status": "ok"}


@router.get("/ready", include_in_schema=False)
async def ready(request: Request) -> dict[str, str]:
    """That this process can serve: its substrate answers and its schema is the one it was built
    for. Raises otherwise, which a readiness probe reads as not ready."""
    await database_of(request).check()
    return {"status": "ready"}


@router.post("/client-events", status_code=204)
async def record_client_events(body: ClientEvents) -> Response:
    """Failures the browser saw, written to this process's log. Public, and so untrusted:
    `ClientEvent` takes no free text. `docs/adr/template/0009`."""
    for event in body.events:
        log.client_event(event)
    return Response(status_code=204)
