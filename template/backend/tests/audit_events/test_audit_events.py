"""The audit event a store builds, apart from any substrate."""

import pytest

from app.audit_events import UnrecordableActor, audit_event
from app.identity.shared import Actor

PERSON = Actor(issuer="a-test", subject="a-person", name="A person")
AGENT = Actor(issuer="tasks", subject="an-agent", name="An agent", on_behalf_of=PERSON)


def test_an_event_names_one_level_of_on_behalf_of() -> None:
    event = audit_event(AGENT, "read", "tasks", ("1",))

    assert event.actor.on_behalf_of == PERSON


def test_an_event_refuses_an_actor_two_levels_deep() -> None:
    """A deeper chain has nowhere to go in a row, and dropping a level would forget a person."""
    nested = Actor(issuer="tasks", subject="a-sub-agent", name="A sub-agent", on_behalf_of=AGENT)

    with pytest.raises(UnrecordableActor, match="a-person"):
        audit_event(nested, "read", "tasks", ("1",))
