"""Who a request is, and therefore which tenant its data belongs to.

One function, and it is the only place in the tree that decides. It authenticates nothing:
every request resolves to `SENTINEL_TENANT`, which is what a deployment with one tenant and no
login needs, and it is a stub rather than a design.

**A function rather than a Protocol, deliberately.** One implementation behind an interface
proves nothing — the interesting rules are exactly the ones a single implementation satisfies
by accident. The seam that matters already exists: routes reach this through a FastAPI
dependency, so swapping it is an edit here and nowhere else. Introduce the Protocol when there
is a second resolver to hold to it.

**What this is not is a trap**, because `tenant_id` is on every row from the first migration.
Going from here to real authentication is a change to this function and a configuration
change; it is never a data migration.

One rule outlives the stub and belongs to every implementation that replaces it:
**an unresolvable credential is never an anonymous principal.** A missing, expired or
malformed credential is a refusal — never a quiet fall back to the sentinel, which would hand
one tenant's data to anybody who failed to log in.
"""

from typing import Final

from fastapi import Request

SENTINEL_TENANT: Final = "default"
"""The tenant every row carries in a deployment that has not wired up authentication.

A named constant rather than an empty string or a null: `tenant_id` is `NOT NULL` and refuses
the empty string, and the eventual migration to real tenancy is an unambiguous
`UPDATE ... WHERE tenant_id = 'default'` only because the value was never ambiguous.
"""


def tenant_for(_request: Request) -> str:
    """Which tenant this request may see. Replace the body; keep the contract.

    The contract is that this returns a tenant or raises. It must never return the sentinel as
    a fallback from a credential it failed to understand.

    The parameter is underscored because this implementation authenticates nothing and so has
    no use for it. Anything that replaces this reads a header, a cookie or a session from it,
    and drops the underscore -- FastAPI injects on the annotation, not the name.
    """
    return SENTINEL_TENANT
