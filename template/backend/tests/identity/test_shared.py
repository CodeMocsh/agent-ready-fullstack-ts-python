"""What every resolver shares, held apart from the resolver the project owns.

These hold whatever a project put in `principal_for`, so `identity_stub` does not cover this file.
"""

import ast
from pathlib import Path

from app import identity
from app.identity import shared


def _defined_in(module: Path) -> set[str]:
    """Every name a module binds at its top level by defining or assigning it, not importing it."""
    names: set[str] = set()
    for node in ast.parse(module.read_text()).body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Assign):
            names.update(one.id for one in node.targets if isinstance(one, ast.Name))
    return names


MECHANISM = sorted(name for name in _defined_in(Path(shared.__file__)) if not name.startswith("_"))
"""Every public name `app/identity/shared.py` defines."""


def test_the_mechanism_is_read_off_the_shared_module() -> None:
    """Without these names, the test below passes over an empty list."""
    assert {"Actor", "Principal", "Unauthenticated", "SENTINEL_TENANT"} <= set(MECHANISM)


def test_app_identity_defines_none_of_the_mechanism_itself() -> None:
    """`app/identity/__init__.py` takes every name of `app/identity/shared.py` it has from there.

    A copy is one the template's code never sees: `app/main.py` answers the shared
    `Unauthenticated` with a `401`, and a project's own class of that name reaches the client as
    a `500`. A constant is read off the source, because two equal strings may be one object.
    """
    defined = _defined_in(Path(identity.__file__)) & set(MECHANISM)
    imported = {
        name
        for name in MECHANISM
        if not isinstance(getattr(shared, name), str)
        and getattr(identity, name, getattr(shared, name)) is not getattr(shared, name)
    }
    own = sorted(defined | imported)

    assert own == [], (
        f"app/identity/__init__.py has its own {own}. Delete them and import them from "
        f"app.identity.shared."
    )


def test_an_actor_is_named_by_its_issuer_and_subject_together() -> None:
    """Two issuers may each vouch for the same subject, and they are two actors."""
    google = shared.Actor(issuer="google", subject="1234", name="Ada")
    microsoft = shared.Actor(issuer="microsoft", subject="1234", name="Ada")

    assert google != microsoft


def test_an_actor_says_who_it_acted_for() -> None:
    """An app that acts for a person keeps that person as an actor, not only as part of a name."""
    person = shared.Actor(issuer="google", subject="1234", name="Ada")
    agent = shared.Actor(issuer="tasks", subject="assistant", name="Assistant", on_behalf_of=person)

    assert agent.on_behalf_of == person
    assert shared.SENTINEL_PRINCIPAL.actor.on_behalf_of is None
