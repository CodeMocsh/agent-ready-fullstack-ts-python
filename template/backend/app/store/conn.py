"""How a connection is named, and which schema it points at.

`Conn` is the whole surface `ddl.py` and `migrate.py` need, so nothing there imports a
driver — and `migrate()` is drivable by a fake in a test with no Postgres in it.

The schema is the other half. This project's DDL is written **unqualified** for legibility,
which is only safe because the schema is selected on the connection: one place to get right
rather than one per statement. It is set to this project's schema **alone**, never
`app, public` — with `public` on the path, `CREATE TABLE IF NOT EXISTS tasks` would find
some other component's `tasks` and skip, which is silent corruption rather than an error.
"""

import hashlib
import os
import re
from typing import Any, Protocol, runtime_checkable

DEFAULT_SCHEMA = "app"
"""Never `public`. Two things sharing `public` collide on the first table name they agree on."""

SCHEMA_ENV = "DB_SCHEMA"

_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class InvalidSchemaName(ValueError):
    """`DB_SCHEMA` is not a plain lowercase identifier.

    The name is interpolated into DDL — no driver parameterises an identifier — so it is
    validated at the boundary rather than escaped and hoped for.
    """


@runtime_checkable
class Conn(Protocol):
    """The three methods the schema work needs. asyncpg's `Connection` satisfies it."""

    async def execute(self, query: str, *args: Any) -> Any: ...

    async def fetchval(self, query: str, *args: Any) -> Any: ...


@runtime_checkable
class Rows(Conn, Protocol):
    """`Conn` plus the row readers the store needs.

    Kept separate because `Conn` is deliberately the *smallest* surface `migrate()` needs —
    small enough that a fake with no database behind it can drive the whole migration path in
    a hermetic test. Widening it would take that away to serve modules that are not the
    migration.
    """

    async def fetch(self, query: str, *args: Any) -> Any: ...

    async def fetchrow(self, query: str, *args: Any) -> Any: ...


def resolve_schema(name: str | None = None) -> str:
    """The validated schema name: the argument, else `DB_SCHEMA`, else `app`."""
    resolved = name if name is not None else os.environ.get(SCHEMA_ENV, DEFAULT_SCHEMA)
    if not _IDENT.match(resolved):
        raise InvalidSchemaName(
            f"{SCHEMA_ENV}={resolved!r}: expected a lowercase identifier matching "
            f"{_IDENT.pattern} (letters, digits and underscore, 63 max)"
        )
    return resolved


def quote_ident(name: str) -> str:
    """Quote a validated identifier, so a schema named like a keyword still works."""
    return f'"{name}"'


def search_path_sql(schema: str | None = None) -> str:
    """`SET search_path` to this project's schema alone. See the module docstring for alone."""
    return f"SET search_path TO {quote_ident(resolve_schema(schema))}"


def migration_lock(schema: str | None = None) -> int:
    """The advisory lock key that serialises migrations, derived from the schema name.

    Derived rather than a literal, and from the schema rather than the project, because an
    advisory lock key is global to the *database*: two deployments sharing one database are
    distinguished by their schema and nothing else, so keying the lock to a constant would
    make each one wait on the other's migration for no reason. Keying it to the schema also
    means the number cannot drift between releases, which is the property a literal is
    usually protecting.
    """
    digest = hashlib.sha256(f"{resolve_schema(schema)}.migrate".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)
