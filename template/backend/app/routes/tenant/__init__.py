"""Every route that requires a tenant.

The router carries `tenant_for`, so every route included here resolves a tenant before its
handler runs (`docs/adr/0008`). A module under this package declares a bare `APIRouter()` and is
included here; `tests/routes/test_guarantee.py` fails on a route that answers without a tenant.
"""

from fastapi import APIRouter, Depends

from app.identity import tenant_for
from app.routes.tenant import tasks

router = APIRouter(dependencies=[Depends(tenant_for)])

router.include_router(tasks.router)
