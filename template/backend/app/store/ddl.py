"""The whole database, as data.

One statement per entry, so the schema is assertable without a Postgres to apply it to and
`make schema` is a `join`. Keys are four-digit and banded by table — `0010_tasks` and its
indexes, repairs at `0200_` — so a new entry sorts where it belongs. `apply` runs every entry
in key order on every release step, so the key decides *when* an entry runs and never *whether*.

Each rule below exists because breaking it fails somewhere far from the edit:

- **Additive and idempotent.** Every entry carries `IF NOT EXISTS`, or is a catalog-guarded
  `DO` block, which is how a statement with no such spelling is made re-runnable at all.

- **Never edit a shipped entry.** `CREATE TABLE IF NOT EXISTS` skips *entirely* once the
  table exists, so a column added to a `CREATE` after that table was ever applied reaches no
  database that already had it. A column therefore lands **twice**: in its table's `CREATE`,
  which stays the whole truth about that table, and as an `ALTER TABLE … ADD COLUMN IF NOT
  EXISTS` repair entry in the `0200_` band, never beside its table. Both statements are
  idempotent, so each is a no-op for the case the other covers.
  `test_every_repair_is_in_step_with_its_create` holds the pair together, and
  `test_no_shipped_entry_body_has_changed` refuses the edit that skips the repair.

Statements are written **unqualified**; `conn.search_path_sql` selects the schema.
`0001_schema` is the one entry that names it, because the search path cannot select a schema
that does not exist yet.
"""

from app.store.conn import quote_ident, resolve_schema

SCHEMA_ENTRY_KEY = "0001_schema"
"""The one entry that names the schema, and the one that has to run first."""

REPAIR_BAND = "0200_"
"""Where an `ALTER TABLE … ADD COLUMN` goes. Above the table it repairs, so the table exists
by the time it runs, and grouped so a reader sees every repair in one place."""

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
  tenant_id   text NOT NULL,
  -- Ordering is `seq`, not `created_at`: now() is transaction start time, so two rows
  -- inserted in one transaction tie on it and the list order becomes arbitrary.
  seq         bigserial NOT NULL,
  title       text NOT NULL,
  done        boolean NOT NULL DEFAULT false,
  created_at  timestamptz NOT NULL DEFAULT now(),
  -- An unset GUC reads as the empty string after a pool issues RESET ALL, so a row
  -- carrying one would be visible to every connection that had not set a tenant. The
  -- policy alone does not stop that; this does.
  CONSTRAINT tasks_tenant_id_not_empty CHECK (tenant_id <> \'\'),
  -- The target a child table\'s foreign key must name. REFERENCES tasks(id) alone lets a
  -- child row point at another tenant\'s parent.
  UNIQUE (id, tenant_id)
)""",
    ),
    (
        "0011_tasks_seq_idx",
        "CREATE UNIQUE INDEX IF NOT EXISTS tasks_seq_idx ON tasks (tenant_id, seq)",
    ),
]
"""Idempotent, additive, one statement each.

All of them run in key order on every release step. `apply` does not filter against the ledger,
it leans on every entry being idempotent, so a database that is already current is unchanged by
the run."""


def statements(schema: str | None = None) -> list[tuple[str, str]]:
    """`SCHEMA` with the one schema-naming entry filled in, sorted by key."""
    name = resolve_schema(schema)
    filled = {SCHEMA_ENTRY_KEY: {"schema": quote_ident(name), "schema_name": f"'{name}'"}}
    return sorted((key, sql.format(**filled[key]) if key in filled else sql) for key, sql in SCHEMA)


TENANT_GUC = "app.tenant_id"
"""The connection setting every policy reads.

Fixed rather than derived from the schema name: it is a property of the *connection*, and one
connection serves one tenant no matter how many schemas it can reach. Unset it reads as NULL,
and after a pool issues `RESET ALL` it reads as the empty string. Neither matches any row --
NULL because nothing equals NULL, and the empty string because `tasks_tenant_id_not_empty`
refuses to store one.
"""

TENANT_TABLES: tuple[str, ...] = ("tasks",)
"""Every table holding tenant data. `applied_once` is the ledger and is deliberately absent:
the schema version is not anybody's data, and a policy on it would hide the ledger from the
role whose only job is to read it."""


def _policy_sql(table: str) -> str:
    """One `FOR ALL` policy, created once -- Postgres has no `CREATE POLICY IF NOT EXISTS`.

    `WITH CHECK` is spelled out even though it is redundant here. With `FOR ALL` and a `USING`
    clause, Postgres applies `USING` to new rows as well, so removing this line changes
    nothing today -- verified by removing it and watching the whole isolation suite still
    pass. It is written because it stops being redundant the moment somebody splits the policy
    per command or narrows `USING`, and a policy whose two expressions disagree is exactly
    where a tenant inserts a row it cannot then read: a corruption that reports itself as
    success.

    Not scoped `TO <schema>_app`. Naming a role here would drag role names into the migration
    and make it require the roles to exist; with one application role there is nothing for the
    planner to skip, which is the only thing that scoping buys.
    """
    name = f"{table}_tenant_isolation"
    scope = f"tenant_id = current_setting('{TENANT_GUC}', true)"
    return (
        "DO $app$\nBEGIN\n"
        "  IF NOT EXISTS (SELECT 1 FROM pg_policies\n"
        "                 WHERE schemaname = current_schema()\n"
        f"                   AND tablename = '{table}'\n"
        f"                   AND policyname = '{name}') THEN\n"
        f"    CREATE POLICY {name} ON {table} FOR ALL\n"
        f"      USING ({scope})\n"
        f"      WITH CHECK ({scope});\n"
        "  END IF;\nEND\n$app$"
    )


def _force_sql(table: str) -> str:
    """`FORCE` as well as `ENABLE`, or the policy is inert for the table's owner.

    A table's owner bypasses its own policies by default, and on a database where an ordinary
    role applied the schema that owner is also the role querying it. `ENABLE` alone therefore
    reads every row, silently, with nothing anywhere reporting a problem.
    """
    return f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY, FORCE ROW LEVEL SECURITY"


SCHEMA.extend((f"{100 + i:04d}_{t}_policy", _policy_sql(t)) for i, t in enumerate(TENANT_TABLES))
SCHEMA.extend((f"{120 + i:04d}_{t}_force", _force_sql(t)) for i, t in enumerate(TENANT_TABLES))
"""Every policy is created before any table is forced. The other order would enforce a table
during the window before its own policy exists, and every statement in it would fail."""
