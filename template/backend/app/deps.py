"""What a route is handed: the tenant it resolved, and a store scoped to that tenant. Neither
resolves anything on its own; both reach the identity seam.

The substrate is chosen once, by `wiring.build()`, and held on the app's lifespan. Reading it
from the request rather than importing a module-level singleton is what lets one test process
hold two apps on two substrates, and it is why there is no `reset()` on any store: a test that
wants a clean database builds a clean app.
"""

from typing import Annotated

from fastapi import Depends, Request

from app import log
from app.identity import tenant_for
from app.store import Database, TaskStore


def database_of(request: Request) -> Database:
    """The substrate this app is serving from.

    Raises rather than reaching for a default. A request that arrives before the lifespan has
    run is a wiring bug, and answering it from a substrate nobody configured would hide that
    bug behind a working response.
    """
    database: Database | None = getattr(request.app.state, "database", None)
    if database is None:
        raise RuntimeError("no database on app.state: the lifespan has not run")
    return database


async def resolved_tenant(request: Request, tenant: Annotated[str, Depends(tenant_for)]) -> str:
    """The tenant `tenant_for` resolved, named on this request's `request completed` line.

    The tenant requirement's router carries this, so every route under it names its tenant, and
    an override of `tenant_for` reaches every route under it. `docs/adr/template/0009`.
    """
    log.name_on_request_line(request, log.TENANT_ID, tenant)
    return tenant


TenantDep = Annotated[str, Depends(resolved_tenant)]
"""The tenant this request resolved to, reached through `Depends` rather than called.

Two things follow from that and neither is cosmetic. FastAPI caches a dependency's result for
the life of a request and does not cache a plain call, so a resolver that verifies a signature
is paid for once however many stores a route asks for. And an override reaches *here* — a test
that substitutes the seam substitutes it everywhere it is read, rather than everywhere somebody
remembered to route through.
"""


def get_store(request: Request, tenant: TenantDep) -> TaskStore:
    """The store this request may use, already scoped to its tenant.

    Resolving the tenant here rather than in each route is what makes it impossible for a
    route to forget: there is no unscoped store to obtain.
    """
    return database_of(request).store(tenant)


StoreDep = Annotated[TaskStore, Depends(get_store)]
