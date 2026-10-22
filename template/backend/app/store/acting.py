"""Who acts on the rows a connection writes, as the settings `audit_row_change` reads.

The settings are session settings. The pool sets all of them on every connection it lends.
`docs/adr/template/0017`.
"""

from typing import Final

from app.identity.shared import Actor
from app.store.ddl import ACTING_GUCS

SET_ACTING: Final = "SELECT " + ", ".join(
    f"set_config('{name}', ${index}, false)" for index, name in enumerate(ACTING_GUCS, start=1)
)
"""Sets every one of `ACTING_GUCS`, in order, from one parameter each."""

MIGRATION_ACTOR: Final = Actor(issuer="release", subject="migrate", name="The release step")
"""Who `make migrate` and `deploy/schema.sql` act as. A row an entry writes is recorded under it."""


def acting(actor: Actor | None, request_id: str | None, trace_id: str | None) -> tuple[str, ...]:
    """The values `SET_ACTING` takes, in the order of `ACTING_GUCS`: empty for each one not
    known."""
    acted_for = None if actor is None else actor.on_behalf_of
    named = {
        "app.actor_issuer": "" if actor is None else actor.issuer,
        "app.actor_subject": "" if actor is None else actor.subject,
        "app.actor_name": "" if actor is None else actor.name,
        "app.on_behalf_of_issuer": "" if acted_for is None else acted_for.issuer,
        "app.on_behalf_of_subject": "" if acted_for is None else acted_for.subject,
        "app.on_behalf_of_name": "" if acted_for is None else acted_for.name,
        "app.request_id": "" if request_id is None else request_id,
        "app.trace_id": "" if trace_id is None else trace_id,
    }
    return tuple(named[name] for name in ACTING_GUCS)


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


MIGRATION_ACTING_SQL: Final = "SELECT " + ", ".join(
    f"set_config('{name}', {_literal(value)}, false)"
    for name, value in zip(ACTING_GUCS, acting(MIGRATION_ACTOR, None, None), strict=True)
)
"""`SET_ACTING` for `MIGRATION_ACTOR`, as one statement with no parameters."""
