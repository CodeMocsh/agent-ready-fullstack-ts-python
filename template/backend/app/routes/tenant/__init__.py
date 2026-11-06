"""Every route that requires a tenant.

The router carries `resolved_principal`, which reaches `principal_for`, so every route included
here resolves a principal before its handler runs (`docs/adr/template/0004`), and its log line
names that principal's tenant and actor. A module under this package declares a bare
`APIRouter()` and is included here; the route guarantee's test, which ships with the identity
stub, fails on a route that answers without a tenant. Every route here may find no database
connection free, and may be over what the process admits for its principal, so each one declares
`NoConnectionFree` and `TooManyRequests`.
"""

from fastapi import APIRouter, Depends

from app.deps import resolved_principal
from app.refusal import NoConnectionFree, TooManyRequests, responses
from app.routes.tenant import tasks

router = APIRouter(
    dependencies=[Depends(resolved_principal)],
    responses=responses(NoConnectionFree, TooManyRequests),
)

router.include_router(tasks.router)
