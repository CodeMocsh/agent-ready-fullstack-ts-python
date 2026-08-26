"""Who a request is, and therefore which tenant its data belongs to.

One function, and it is the only place in the tree that decides. It authenticates nothing:
every request resolves to `SENTINEL_TENANT`, which is what a deployment with one tenant and no
login needs, and it is a stub rather than a design.

**A function rather than a Protocol, deliberately.** One implementation behind an interface
proves nothing — the interesting rules are exactly the ones a single implementation satisfies
by accident. Introduce the Protocol when there is a second resolver to hold to it.

Every route reaches this because `app/routes.py` declares it on the router rather than on the
handlers. `docs/adr/0008` says why, and what a replacement owes.

**What this is not is a trap**, because `tenant_id` is on every row from the first migration.
Going from here to real authentication is a change to this function and a configuration
change; it is never a data migration.

One rule outlives the stub and belongs to every implementation that replaces it:
**an unresolvable credential is never an anonymous principal.** A missing, expired or
malformed credential raises `Unauthenticated` — never a quiet fall back to the sentinel, which
would hand one tenant's data to anybody who failed to log in.

Anything finer than the tenant is enforced beside a route and never in a policy: `docs/adr/0002`
permits a policy to compare a column to a setting and nothing else.
"""

import inspect
from typing import Final

from fastapi import Request


class Unauthenticated(Exception):
    """No tenant could be resolved, and the request must be refused rather than served.

    The sentinel never raises it, because a deployment that authenticates nothing has nothing
    to refuse. It is declared here anyway, so `app/main.py` can answer it as a `401` from the
    first commit: an implementation that has to remember its own handler is one that returns
    `500` to every unauthenticated request until somebody notices, and a `500` is the answer a
    client retries.

    It carries what to say and nothing beyond it. The detail reaches the browser, and the
    difference between "expired" and "signed by the wrong key" is a fact about a credential
    whoever is asking did not present.
    """


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


async def resolved_without_a_credential() -> str | None:
    """The tenant a request carrying nothing resolves to, or `None` when it is refused.

    A property rather than a flag, and it awaits: a replacement is written `async def`, and
    calling one returns a coroutine rather than raising. `docs/adr/0008` records both, and what
    reading that coroutine as a tenant did. Anything that is not a string is `None`, and every
    exception is silence -- a resolver that did not hand out a tenant did not serve the request.
    """
    try:
        resolved = tenant_for(_a_request_carrying_nothing())
        if inspect.isawaitable(resolved):
            resolved = await resolved
    except Exception:
        return None
    return resolved if isinstance(resolved, str) else None


def _a_request_carrying_nothing() -> Request:
    """The smallest request the seam's signature accepts, with no header to believe."""
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})
