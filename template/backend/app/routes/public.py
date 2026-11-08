"""The routes that may answer without resolving a tenant.

Which ones is a list in the route guarantee's test, rather than a mark here.
`docs/adr/template/0004` says why an exemption is a list and never a decorator.
"""

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from app import log, reads
from app.deps import database_of
from app.models import ClientEvents, Reads
from app.refusal import GroupTooLarge, responses

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
    `ClientEvent` takes no free text. `docs/adr/template/0011`."""
    for event in body.events:
        log.client_event(event)
    return Response(status_code=204)


@router.get("/reads", response_model=Reads, responses=responses(GroupTooLarge))
async def read_parts(
    request: Request,
    part: Annotated[list[str], Query(min_length=1, max_length=reads.PATHS_AT_MOST)],
) -> Response:
    """The answer of each `part`, in order, as its route answers it alone. Requires nothing
    itself: each part carries the credential of this request to its own route. A part that cannot
    answer in the group answers `left`. `docs/adr/template/0029`."""
    return await reads.answer(request, part)
