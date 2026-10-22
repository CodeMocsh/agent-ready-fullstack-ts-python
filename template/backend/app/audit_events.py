"""Who changed which rows, and who made which request.

Two kinds of audit event, and nothing a project writes records either one:

- A **row event** names one statement's writes to one tenant's rows. Postgres records it, in the
  transaction that wrote the rows, with a trigger on every table in `TENANT_TABLES`. It names the
  actor this module names as acting. The trigger refuses a write when no actor is acting.
- A **request event** names one request a principal made: its method, its route, the values of
  its path parameters and its status. `app/request_events.py` records it before the response
  leaves.

An event names rows by id and fields by name, and never holds a value a row holds. The template
owns this module, and an update brings it to every project. `docs/adr/template/0017`.
"""

import asyncio
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import ClassVar, Final, Literal

from fastapi import Request
from pydantic import BaseModel, ConfigDict

from app import log
from app.identity.shared import Actor, Principal

Action = Literal["read", "create", "update", "delete"]
"""What a row event did to its rows. `read` is on events a store recorded before 0.42.0."""

Outcome = Literal["done", "absent"]
"""`done` on every row event. `absent` is on events a store recorded before 0.42.0 for an
operation that found no row."""

SERVED: Final = "served_principal"
"""The name `serve` keeps a request's principal under, in the request's state."""

_ACTING: ContextVar[Actor | None] = ContextVar("acting", default=None)


class UnrecordableActor(ValueError):
    """The actor acted for an actor who acted for somebody else. An audit event records one
    level of `on_behalf_of`."""


class ServedOffTheLoop(RuntimeError):
    """`serve` was called outside the event loop, as from a `def` dependency. The actor it names
    does not reach the route."""


class RequestEvent(BaseModel):
    """One request a principal made, as the request layer records it.

    `route` is the template of the route that answered, and `resource_ids` the values of its path
    parameters, in the order the route names them. `request_id` and `trace_id` are `None`
    outside a sampled trace or a request id.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    actor: Actor
    method: str
    route: str
    resource_ids: tuple[str, ...]
    status: int
    request_id: str | None
    trace_id: str | None


class RecordedAuditEvent(BaseModel):
    """An audit event as a substrate reads it back, of either kind.

    A row event has `action`, `outcome` and `resource_type`, the table it wrote. `resource_ids`
    are the rows it wrote, and `fields` the columns it changed. A request event has `method`,
    `route` and `status`. Its `resource_ids` are the values of its path parameters, and its
    `fields` are empty.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    tenant_id: str
    recorded_at: datetime
    actor: Actor
    action: Action | None
    outcome: Outcome | None
    resource_type: str | None
    method: str | None
    route: str | None
    status: int | None
    resource_ids: tuple[str, ...]
    fields: tuple[str, ...]
    request_id: str | None
    trace_id: str | None


def recordable(actor: Actor) -> Actor:
    """`actor`. Raises `UnrecordableActor` for an actor whose `on_behalf_of` acted for somebody
    else."""
    if actor.on_behalf_of is not None and actor.on_behalf_of.on_behalf_of is not None:
        raise UnrecordableActor(
            f"{actor.subject!r} acted for {actor.on_behalf_of.subject!r}, who acted for "
            f"{actor.on_behalf_of.on_behalf_of.subject!r}. Name the person in on_behalf_of."
        )
    return actor


def current_actor() -> Actor | None:
    """The actor every write here acts as, and `None` where nothing named one: a write then
    fails."""
    return _ACTING.get()


@contextmanager
def acting_as(actor: Actor) -> Generator[Actor]:
    """Act as `actor` inside the block: every row it writes is recorded under `actor`.

    Work outside a request runs inside one, or its first write fails. An app acting for a person
    names the person in `on_behalf_of`. Raises `UnrecordableActor` for an actor two levels deep.
    """
    token = _ACTING.set(recordable(actor))
    try:
        yield actor
    finally:
        _ACTING.reset(token)


def serve(request: Request, principal: Principal) -> Principal:
    """Make `principal` who this request is: named on its `request completed` line, the actor of
    every row it writes, and the actor of its request event.

    The identity dependency calls it once per request, and is an `async def`. Raises
    `ServedOffTheLoop` from a `def` dependency, `UnrecordableActor` for an actor two levels deep,
    and `AttributeError` for a request `log.instrument` is not serving.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError as off_the_loop:
        raise ServedOffTheLoop(
            "audit_events.serve ran in a worker thread, so its actor would not reach the route. "
            "Call it from an async def dependency."
        ) from off_the_loop
    recordable(principal.actor)
    log.name_principal(request, principal)
    _ACTING.set(principal.actor)
    setattr(request.state, SERVED, principal)
    return principal
