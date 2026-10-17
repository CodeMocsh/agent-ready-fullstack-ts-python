"""Resolvers that stand in the identity seam, so a test can hold this app to a rule the shipped
stub cannot demonstrate.

`refusing` is the second resolver. The one that ships authenticates nothing and resolves
everybody, which is honest and is also unable to show that a route is guarded -- every route
answers `200` whether it reached the seam or walked past it. Substituting this one is what lets
`tests/routes/test_guarantee.py` assert the guarantee behaviourally.

These belong to the stub, and `identity_stub` covers this file with it.
"""

from fastapi import Request

from app.identity.shared import Actor, Principal, Unauthenticated

REFUSAL = "this request carries no credential"
"""What the double says. Asserted on, so the refusal reaching the client is the one raised
here rather than a `401` from somewhere else in the stack that happens to look the same."""


def refusing(_request: Request) -> Principal:
    """What every resolver owes a credential it cannot resolve. It never
    returns."""
    raise Unauthenticated(REFUSAL)


async def refusing_async(_request: Request) -> Principal:
    """The same refusal, from the shape a real resolver actually takes.

    Verifying a credential reaches a session store or a key set, so `async def` is what a
    replacement is written as — and calling one returns a coroutine instead of raising. Read as
    a principal, that told a fully authenticating deployment it authenticated nothing, on every
    boot, naming a coroutine object. Every test that substituted only a synchronous resolver passed
    straight over it, which is why this one exists.
    """
    raise Unauthenticated(REFUSAL)


AN_ASYNC_PRINCIPAL = Principal(
    tenant_id="an-async-tenant",
    actor=Actor(issuer="a-test", subject="an-async-actor", name="An async actor"),
)


async def resolving_async(_request: Request) -> Principal:
    """An `async` resolver that hands out a principal: still a deployment that authenticates
    nothing, and it has to be reported as one rather than as a coroutine."""
    return AN_ASYNC_PRINCIPAL


def failing(_request: Request) -> Principal:
    """A resolver that breaks in its own way rather than refusing politely. Anything other than
    handing out a principal is a resolver that did not serve the request."""
    raise RuntimeError("a resolver failing in its own way")
