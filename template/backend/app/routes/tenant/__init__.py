"""Every route that requires a tenant.

The router carries `resolved_principal`, which reaches `principal_for`, so every route included
here resolves a principal before its handler runs (`docs/adr/template/0004`), and its log line
names that principal's tenant and actor. A module under this package declares a bare
`APIRouter()` and is included here; the route guarantee's test, which ships with the identity
stub, fails on a route that answers without a tenant.
"""

from fastapi import APIRouter, Depends

from app.deps import resolved_principal
from app.routes.tenant import tasks

router = APIRouter(dependencies=[Depends(resolved_principal)])

router.include_router(tasks.router)
