"""Who a request is: the tenant its data belongs to, and the actor it acts as.

`principal_for` is the identity seam: the one place in the tree that decides. As the template
ships it, it is the identity stub, and it resolves every request to `SENTINEL_PRINCIPAL`.
Replace its body to authenticate.

The template updates this module until the project records `identity_stub: false`, and the
project owns it from then on. It re-exports `app/identity/shared.py` and defines no name that
module defines. Every route reaches `principal_for` because `app/routes/tenant/` declares it on
the router. `docs/adr/template/0004`.
"""

from fastapi import Request

from app.identity.shared import (
    SENTINEL_PRINCIPAL as SENTINEL_PRINCIPAL,
    SENTINEL_TENANT as SENTINEL_TENANT,
    Actor as Actor,
    Principal as Principal,
    Unauthenticated as Unauthenticated,
    resolved_without_a_credential as resolved_without_a_credential,
)


def principal_for(_request: Request) -> Principal:
    """Who this request is. Replace the body; keep the contract.

    Returns a principal, or raises `Unauthenticated` for a missing, expired or malformed
    credential. It never returns the sentinel principal for a credential it could not resolve.
    A replacement reads the credential from the request and drops the underscore: FastAPI
    injects on the annotation, not the name.
    """
    return SENTINEL_PRINCIPAL
