"""What every resolver of the identity seam shares: the principal it returns, the refusal it
raises, the sentinel principal, and the probe that asks whether a resolver authenticates.

The template owns this module, and an update brings it to every project. Template code imports
these names from here, and only `principal_for` from `app/identity/__init__.py`.
`docs/adr/template/0004`.
"""

import inspect
from collections.abc import Callable
from typing import ClassVar, Final

from fastapi import Request
from pydantic import BaseModel, ConfigDict


class Unauthenticated(Exception):
    """No principal could be resolved, so the request is refused.

    `app/main.py` answers it with a `401`. Its message reaches the client: it says what to do,
    and nothing about why the credential failed.
    """


class Actor(BaseModel):
    """Who acted: a person, or an app that acts for itself or for a person.

    `issuer` is the project's name for whoever vouched for `subject`. `(issuer, subject)`
    identifies the actor, and `subject` alone does not. `name` is the display name the actor
    carried when it acted. `on_behalf_of` is the actor this one acted for, and `None` when it
    acted for itself.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    issuer: str
    subject: str
    name: str
    on_behalf_of: "Actor | None" = None


class Principal(BaseModel):
    """Who a request is: the tenant it may see, and the actor it acts as. Only a resolver builds
    one."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    tenant_id: str
    actor: Actor


SENTINEL_TENANT: Final = "default"
"""The tenant every row carries in a deployment that has not wired up authentication."""

SENTINEL_PRINCIPAL: Final = Principal(
    tenant_id=SENTINEL_TENANT,
    actor=Actor(issuer="sentinel", subject="default", name="Unauthenticated"),
)
"""What the identity stub resolves every request to."""


async def resolved_without_a_credential(resolver: Callable[[Request], object]) -> Principal | None:
    """The principal `resolver` hands a request that carries nothing, or `None` when it refuses.

    Awaits a resolver written `async def`. Anything that is not a `Principal` is `None`, and every
    exception is `None`: a resolver that did not hand out a principal did not serve the request.
    """
    try:
        resolved = resolver(_a_request_carrying_nothing())
        if inspect.isawaitable(resolved):
            resolved = await resolved
    except Exception:
        return None
    return resolved if isinstance(resolved, Principal) else None


def _a_request_carrying_nothing() -> Request:
    """The smallest request the seam's signature accepts, with no header to believe."""
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})
