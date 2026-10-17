"""Who read or changed which rows, and in which request.

A store builds one `AuditEvent` for each operation that reads or changes rows, and records it
in the same transaction as the operation. When the event cannot be recorded, the operation
fails and changes nothing. An event names rows by id and fields by name, and never holds a
value a row holds. The template owns this module, and an update brings it to every project.
`docs/adr/template/0017`.
"""

from datetime import datetime
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from app import log
from app.identity.shared import Actor

Action = Literal["read", "create", "update", "delete"]


class UnrecordableActor(ValueError):
    """The actor acted for an actor who acted for somebody else. An audit event records one
    level of `on_behalf_of`."""


class AuditEvent(BaseModel):
    """One operation on rows, as a store records it.

    `resource_ids` are the rows the operation returned or changed, in the order it returned
    them. `fields` are the names of the fields it wrote. `request_id` and `trace_id` are `None`
    for an operation outside a request or outside a sampled trace.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    actor: Actor
    action: Action
    resource_type: str
    resource_ids: tuple[str, ...]
    fields: tuple[str, ...]
    request_id: str | None
    trace_id: str | None


class RecordedAuditEvent(AuditEvent):
    """An audit event as a substrate reads it back: the tenant it belongs to, and the time the
    substrate recorded it."""

    tenant_id: str
    recorded_at: datetime


def written(body: BaseModel) -> tuple[str, ...]:
    """The names of the fields the caller set in `body`, in name order."""
    return tuple(sorted(body.model_fields_set))


def audit_event(
    actor: Actor,
    action: Action,
    resource_type: str,
    resource_ids: tuple[str, ...],
    fields: tuple[str, ...] = (),
) -> AuditEvent:
    """The event for one operation, naming the request and the trace it runs in.

    Raises `UnrecordableActor` for an actor whose `on_behalf_of` acted for somebody else.
    """
    if actor.on_behalf_of is not None and actor.on_behalf_of.on_behalf_of is not None:
        raise UnrecordableActor(
            f"{actor.subject!r} acted for {actor.on_behalf_of.subject!r}, who acted for "
            f"{actor.on_behalf_of.on_behalf_of.subject!r}. Name the person in on_behalf_of."
        )
    return AuditEvent(
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_ids=resource_ids,
        fields=fields,
        request_id=log.current_request_id(),
        trace_id=log.current_trace_id(),
    )
