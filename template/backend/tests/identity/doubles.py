"""Stand-ins for the identity seam, so a test can hold this app to a rule the shipped stub cannot
demonstrate.

`refusing` is the second implementation of the seam. The one that ships authenticates nothing
and resolves everybody, which is honest and is also unable to show that a route is guarded --
every route answers `200` whether it reached the seam or walked past it. Substituting this one is
what lets `tests/routes/test_guarantee.py` assert the guarantee behaviourally.

These belong to the stub, and `identity_stub` covers this file with it.
"""

from fastapi import Request

from app.identity import Unauthenticated

REFUSAL = "this request carries no credential"
"""What the double says. Asserted on, so the refusal reaching the client is the one raised
here rather than a `401` from somewhere else in the stack that happens to look the same."""


def refusing(_request: Request) -> str:
    """What every implementation of the seam owes a credential it cannot resolve.

    Annotated as returning `str` so it substitutes for `tenant_for` exactly — FastAPI resolves
    an override against the signature of the dependency it replaces. It never reaches a return.
    """
    raise Unauthenticated(REFUSAL)


async def refusing_async(_request: Request) -> str:
    """The same refusal, from the shape a real resolver actually takes.

    Verifying a credential reaches a session store or a key set, so `async def` is what a
    replacement is written as — and calling one returns a coroutine instead of raising. Read as
    a tenant, that told a fully authenticating deployment it authenticated nothing, on every
    boot, naming a coroutine object. Every test that substituted only a synchronous seam passed
    straight over it, which is why this one exists.
    """
    raise Unauthenticated(REFUSAL)


async def resolving_async(_request: Request) -> str:
    """An `async` seam that hands out a tenant: still a deployment that authenticates nothing,
    and it has to be reported as one rather than as a coroutine."""
    return "an-async-tenant"


def failing(_request: Request) -> str:
    """A seam that breaks in its own way rather than refusing politely. Anything other than
    handing out a tenant is a resolver that did not serve the request."""
    raise RuntimeError("a resolver failing in its own way")
