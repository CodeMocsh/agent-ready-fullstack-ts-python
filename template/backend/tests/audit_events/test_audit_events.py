"""Who acts, apart from any substrate or request."""

import pytest

from app.audit_events import UnrecordableActor, acting_as, current_actor
from app.identity.shared import Actor
from app.store.acting import MIGRATION_ACTING_SQL, SET_ACTING, acting
from app.store.ddl import ACTING_GUCS

PERSON = Actor(issuer="a-test", subject="a-person", name="A person")
AGENT = Actor(issuer="tasks", subject="an-agent", name="An agent", on_behalf_of=PERSON)


def test_nothing_acts_until_something_names_an_actor() -> None:
    assert current_actor() is None


def test_an_actor_acts_inside_its_block_and_not_after() -> None:
    with acting_as(AGENT):
        assert current_actor() == AGENT
        with acting_as(PERSON):
            assert current_actor() == PERSON
        assert current_actor() == AGENT

    assert current_actor() is None


def test_an_actor_two_levels_deep_is_refused() -> None:
    """A deeper chain has nowhere to go in a row, and dropping a level would forget a person."""
    nested = Actor(issuer="tasks", subject="a-sub-agent", name="A sub-agent", on_behalf_of=AGENT)

    with pytest.raises(UnrecordableActor, match="a-person"), acting_as(nested):
        pass


def test_an_app_acting_for_a_person_names_both() -> None:
    assert acting(AGENT, "a-request", None) == (
        "tasks",
        "an-agent",
        "An agent",
        "a-test",
        "a-person",
        "A person",
        "a-request",
        "",
    )


def test_no_actor_sets_every_setting_empty() -> None:
    """Empty, and not left as it was: a connection the pool lends again names nobody."""
    assert acting(None, None, None) == ("",) * len(ACTING_GUCS)


def test_every_setting_is_set_by_both_statements() -> None:
    for name in ACTING_GUCS:
        assert f"set_config('{name}'," in SET_ACTING
        assert f"set_config('{name}'," in MIGRATION_ACTING_SQL
