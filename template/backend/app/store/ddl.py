"""The whole database, as data.

One statement per entry, so the schema is assertable without a Postgres to apply it to and
`make schema` is a `join`. Keys are four-digit and banded by table — `0010_tasks` and its
indexes, repairs at `0200_` — so a new entry sorts where it belongs and `max(key)` is the
version marker.

Three rules, and each one exists because breaking it fails somewhere far from the edit:

- **Additive and idempotent.** Every entry carries `IF NOT EXISTS`, or is a catalog-guarded
  `DO` block, which is how a statement with no such spelling is made re-runnable at all.

- **Never edit a shipped entry.** `CREATE TABLE IF NOT EXISTS` skips *entirely* once the
  table exists, so a column added to a `CREATE` after that table was ever applied reaches no
  database that already had it. A column therefore lands **twice**: in its table's `CREATE`,
  which stays the whole truth about that table, and as an `ALTER TABLE … ADD COLUMN IF NOT
  EXISTS` repair entry in the `0200_` band, never beside its table. Both statements are
  idempotent, so each is a no-op for the case the other covers, and
  `test_a_column_added_by_an_alter_is_also_in_its_create` holds the pair together.

- **The repair band sorts above everything.** `migrate()` compares `max(key)` and returns
  early when the database is not behind, so a repair that sorted under an existing entry
  would never run in either mode.

Statements are written **unqualified**; `conn.search_path_sql` selects the schema.
`0001_schema` is the one entry that names it, because the search path cannot select a schema
that does not exist yet.
"""

from app.store.conn import quote_ident, resolve_schema

SCHEMA_ENTRY_KEY = "0001_schema"
"""The one entry that names the schema, and the one that has to run first."""

REPAIR_BAND = "0200_"
"""Where an `ALTER TABLE … ADD COLUMN` goes. Above every other band, deliberately."""

APPLIED_ONCE_DDL = """CREATE TABLE IF NOT EXISTS applied_once (
  key         text PRIMARY KEY,
  applied_at  timestamptz NOT NULL DEFAULT now()
)"""
"""The ledger. Created before the loop that writes to it, and by the same lock holder."""

SCHEMA: list[tuple[str, str]] = [
    (
        SCHEMA_ENTRY_KEY,
        "DO $app$\nBEGIN\n"
        "  IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = {schema_name}) THEN\n"
        "    CREATE SCHEMA {schema};\n"
        "  END IF;\nEND\n$app$",
    ),
    (
        "0010_tasks",
        """CREATE TABLE IF NOT EXISTS tasks (
  id          uuid PRIMARY KEY,
  -- Ordering is `seq`, not `created_at`: now() is transaction start time, so two rows
  -- inserted in one transaction tie on it and the list order becomes arbitrary.
  seq         bigserial NOT NULL,
  title       text NOT NULL,
  done        boolean NOT NULL DEFAULT false,
  created_at  timestamptz NOT NULL DEFAULT now()
)""",
    ),
    (
        "0011_tasks_seq_idx",
        "CREATE UNIQUE INDEX IF NOT EXISTS tasks_seq_idx ON tasks (seq)",
    ),
]
"""Idempotent, additive, one statement each.

Applied in order whenever the database is behind, and *all* of them are -- `apply_schema`
does not filter against the ledger, it leans on every entry being idempotent. `migrate()`
returns early when `max(key)` has not moved, so a database that is current runs none."""


def statements(schema: str | None = None) -> list[tuple[str, str]]:
    """`SCHEMA` with the one schema-naming entry filled in, sorted by key."""
    name = resolve_schema(schema)
    filled = {SCHEMA_ENTRY_KEY: {"schema": quote_ident(name), "schema_name": f"'{name}'"}}
    return sorted((key, sql.format(**filled[key]) if key in filled else sql) for key, sql in SCHEMA)
