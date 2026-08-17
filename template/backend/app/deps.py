"""What a route is handed. One dependency, and it resolves nothing on its own.

The substrate is chosen once, by `wiring.build()`, and held on the app's lifespan. Reading it
from the request rather than importing a module-level singleton is what lets one test process
hold two apps on two substrates, and it is why there is no `reset()` on any store: a test that
wants a clean database builds a clean app.
"""

from typing import Annotated

from fastapi import Depends, Request

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


def get_store(request: Request) -> TaskStore:
    """The store this request may use, already scoped to its tenant.

    Resolving the tenant here rather than in each route is what makes it impossible for a
    route to forget: there is no unscoped store to obtain.
    """
    return database_of(request).store(tenant_for(request))


StoreDep = Annotated[TaskStore, Depends(get_store)]
