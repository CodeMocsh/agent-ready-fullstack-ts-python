"""The whole database, as data.

One statement per entry, so the schema is assertable without a Postgres to apply it to and
`make schema` is a `join`. Keys are four-digit and banded by table — `0010_tasks` and its
indexes, policies from `POLICY_BAND`, each `FORCE` from `FORCE_BAND`, the audit triggers from
`AUDIT_BAND`, repairs from `REPAIR_BAND` — so a new entry sorts where it belongs. `apply` runs
every entry in key order on every release step, so the key decides *when* an entry runs and
never *whether*.

Each rule below exists because breaking it fails somewhere far from the edit:

- **Additive and idempotent.** Every entry carries `IF NOT EXISTS`, or is a catalog-guarded
  `DO` block, which is how a statement with no such spelling is made re-runnable at all.

- **Never edit a shipped entry.** `CREATE TABLE IF NOT EXISTS` skips *entirely* once the
  table exists, so a column added to a `CREATE` after that table was ever applied reaches no
  database that already had it. A column therefore lands **twice**: in its table's `CREATE`,
  which stays the whole truth about that table, and as an `ALTER TABLE … ADD COLUMN IF NOT
  EXISTS` repair entry keyed from `REPAIR_BAND`, never beside its table. Both statements are
  idempotent, so each is a no-op for the case the other covers.
  `test_every_repair_is_in_step_with_its_create` holds the pair together.
  `test_no_shipped_entry_body_has_changed` refuses the edited `CREATE` until its hash is
  re-recorded by hand in `.schema-baseline.json`, in the commit that adds the repair.

Statements are written **unqualified**; `conn.search_path_sql` selects the schema.
`0001_schema` is the one entry that names it, because the search path cannot select a schema
that does not exist yet.
"""

from app.store.conn import quote_ident, resolve_schema
from app.store.roles import APPEND_ONLY_TABLE, app_role

SCHEMA_ENTRY_KEY = "0001_schema"
"""The one entry that names the schema, and the one that has to run first."""

APPEND_ONLY_ENTRY_KEY = "0032_audit_events_append_only"
"""The entry that revokes `UPDATE` and `DELETE` on `audit_events` from the application role, on
every release step and in `deploy/schema.sql`. It runs after `0030_audit_events` creates the
table, on which the default privileges grant both. `writes_withheld_sql` in `app/store/roles.py`
now withholds the same and more; this entry stays because a shipped key is never removed.
`docs/adr/template/0017`."""

POLICY_BAND = 100
"""The key of the policy on the table at index `i` of `TENANT_TABLES` is `POLICY_BAND + i`."""

FORCE_BAND = 120
"""The key of the `FORCE` on the table at index `i` of `TENANT_TABLES` is `FORCE_BAND + i`."""

AUDIT_BAND = 140
"""The key of the audit triggers on the table at index `i` of `TENANT_TABLES` is
`AUDIT_BAND + i`. `audit_events` holds none, so its key is never used."""

AUDIT_FUNCTION_ENTRY_KEY = "0033_audit_row_change"
"""The entry that creates `audit_row_change`, which every audit trigger runs."""

REPAIR_BAND = 200
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
    (
        "0030_audit_events",
        """CREATE TABLE IF NOT EXISTS audit_events (
  seq                   bigserial PRIMARY KEY,
  tenant_id             text NOT NULL,
  recorded_at           timestamptz NOT NULL DEFAULT clock_timestamp(),
  actor_issuer          text NOT NULL,
  actor_subject         text NOT NULL,
  actor_name            text NOT NULL,
  on_behalf_of_issuer   text,
  on_behalf_of_subject  text,
  on_behalf_of_name     text,
  action                text,
  outcome               text
    CONSTRAINT audit_events_outcome_known CHECK (outcome IN (\'done\', \'absent\')),
  method                text,
  route                 text,
  status                smallint,
  resource_type         text,
  resource_ids          text[] NOT NULL,
  fields                text[] NOT NULL,
  request_id            text,
  trace_id              text,
  CONSTRAINT audit_events_tenant_id_not_empty CHECK (tenant_id <> \'\'),
  CONSTRAINT audit_events_action_known
    CHECK (action IN (\'read\', \'create\', \'update\', \'delete\')),
  CONSTRAINT audit_events_on_behalf_of_whole CHECK (
    (on_behalf_of_issuer IS NULL) = (on_behalf_of_subject IS NULL)
    AND (on_behalf_of_subject IS NULL) = (on_behalf_of_name IS NULL)
  ),
  CONSTRAINT audit_events_kind_whole CHECK (
    (method IS NULL) = (status IS NULL)
    AND (method IS NULL) = (route IS NULL)
    AND (action IS NULL) = (outcome IS NULL)
    AND (action IS NULL) = (resource_type IS NULL)
    AND (method IS NULL) = (action IS NOT NULL)
  )
)""",
    ),
    (
        "0031_audit_events_seq_idx",
        "CREATE UNIQUE INDEX IF NOT EXISTS audit_events_seq_idx ON audit_events (tenant_id, seq)",
    ),
    (
        APPEND_ONLY_ENTRY_KEY,
        "DO $app$\nBEGIN\n"
        "  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {app_role_name}) THEN\n"
        "    REVOKE UPDATE, DELETE ON audit_events FROM {app_role};\n"
        "  END IF;\nEND\n$app$",
    ),
    (
        AUDIT_FUNCTION_ENTRY_KEY,
        """CREATE OR REPLACE FUNCTION audit_row_change() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $audit$
DECLARE
  issuer text := nullif(current_setting('app.actor_issuer', true), '');
  subject text := nullif(current_setting('app.actor_subject', true), '');
  key text[];
  none constant text := '(SELECT NULL::jsonb AS r WHERE false)';
BEGIN
  IF issuer IS NULL OR subject IS NULL THEN
    RAISE EXCEPTION 'a write to % names no actor', TG_TABLE_NAME
      USING HINT = 'Run the work inside app.audit_events.acting_as(actor).';
  END IF;
  SELECT array_agg(a.attname::text ORDER BY k.ord) INTO key
    FROM pg_index i
    CROSS JOIN unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord)
    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
   WHERE i.indrelid = TG_RELID AND i.indisprimary;
  IF key IS NULL THEN
    RAISE EXCEPTION '% has no primary key, so an audit event cannot name its rows', TG_TABLE_NAME;
  END IF;
  EXECUTE format($sql$
    WITH pairs AS (
      SELECT coalesce(a.r, b.r) ->> 'tenant_id' AS tenant,
             array_to_string(coalesce(a.id, b.id), ':') AS id,
             b.r AS before, a.r AS after
        FROM (SELECT r, ARRAY(SELECT r ->> k FROM unnest($1::text[]) AS k) AS id FROM %s) AS b
        FULL JOIN (SELECT r, ARRAY(SELECT r ->> k FROM unnest($1::text[]) AS k) AS id FROM %s) AS a
          ON a.id = b.id
    ), changed AS (
      SELECT DISTINCT p.tenant, f.name
        FROM pairs AS p CROSS JOIN jsonb_object_keys(coalesce(p.before, p.after)) AS f(name)
       WHERE (p.before -> f.name) IS DISTINCT FROM (p.after -> f.name)
    )
    INSERT INTO audit_events (tenant_id, actor_issuer, actor_subject, actor_name,
      on_behalf_of_issuer, on_behalf_of_subject, on_behalf_of_name, action, outcome,
      resource_type, resource_ids, fields, request_id, trace_id)
    SELECT p.tenant, $2, $3, coalesce(current_setting('app.actor_name', true), ''),
           nullif(current_setting('app.on_behalf_of_issuer', true), ''),
           nullif(current_setting('app.on_behalf_of_subject', true), ''),
           nullif(current_setting('app.on_behalf_of_name', true), ''),
           $4, 'done', $5, array_agg(p.id ORDER BY p.id),
           coalesce((SELECT array_agg(c.name ORDER BY c.name) FROM changed AS c
                      WHERE c.tenant = p.tenant), '{}'),
           nullif(current_setting('app.request_id', true), ''),
           nullif(current_setting('app.trace_id', true), '')
      FROM pairs AS p
     GROUP BY p.tenant
  $sql$,
    CASE WHEN TG_OP = 'INSERT' THEN none ELSE '(SELECT to_jsonb(o) AS r FROM old_rows AS o)' END,
    CASE WHEN TG_OP = 'DELETE' THEN none ELSE '(SELECT to_jsonb(n) AS r FROM new_rows AS n)' END)
  USING key, issuer, subject,
    CASE TG_OP WHEN 'INSERT' THEN 'create' WHEN 'UPDATE' THEN 'update' ELSE 'delete' END,
    TG_TABLE_NAME;
  RETURN NULL;
END
$audit$""",
    ),
]
"""Idempotent, additive, one statement each.

All of them run in key order on every release step. `apply` does not filter against the ledger,
it leans on every entry being idempotent, so a database that is already current is unchanged by
the run."""


def statements(schema: str | None = None) -> list[tuple[str, str]]:
    """`SCHEMA` with the schema and the application role filled in, sorted by key."""
    name = resolve_schema(schema)
    filled = {
        SCHEMA_ENTRY_KEY: {"schema": quote_ident(name), "schema_name": f"'{name}'"},
        APPEND_ONLY_ENTRY_KEY: {
            "app_role": quote_ident(app_role(name)),
            "app_role_name": f"'{app_role(name)}'",
        },
    }
    return sorted((key, sql.format(**filled[key]) if key in filled else sql) for key, sql in SCHEMA)


TENANT_GUC = "app.tenant_id"
"""The connection setting every policy reads.

Fixed rather than derived from the schema name: it is a property of the *connection*, and one
connection serves one tenant no matter how many schemas it can reach. Unset it reads as NULL,
and after a pool issues `RESET ALL` it reads as the empty string. Neither matches any row --
NULL because nothing equals NULL, and the empty string because `tasks_tenant_id_not_empty`
refuses to store one.
"""

ACTING_GUCS: tuple[str, ...] = (
    "app.actor_issuer",
    "app.actor_subject",
    "app.actor_name",
    "app.on_behalf_of_issuer",
    "app.on_behalf_of_subject",
    "app.on_behalf_of_name",
    "app.request_id",
    "app.trace_id",
)
"""The connection settings `audit_row_change` reads: who acts, and in which request and trace.
Empty reads as unset. A write with no actor issuer or subject is refused."""

TENANT_TABLES: tuple[str, ...] = ("tasks", "audit_events")
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


AUDITED_WRITES: tuple[tuple[str, str], ...] = (
    ("insert", "NEW TABLE AS new_rows"),
    ("update", "OLD TABLE AS old_rows NEW TABLE AS new_rows"),
    ("delete", "OLD TABLE AS old_rows"),
)
"""Each write an audit trigger records, and the rows it hands `audit_row_change`. Each write has
a trigger of its own."""


def audit_sql(table: str) -> str:
    """The audit triggers on `table`: one statement-level trigger for each of `AUDITED_WRITES`,
    each created once. Each statement records one audit event for each tenant whose rows it
    wrote, in the transaction that wrote them, and is refused when no actor is set."""
    creates = "".join(
        "  IF NOT EXISTS (SELECT 1 FROM pg_trigger\n"
        f"                 WHERE tgrelid = '{table}'::regclass\n"
        f"                   AND tgname = '{table}_audit_{write}') THEN\n"
        f"    CREATE TRIGGER {table}_audit_{write} AFTER {write.upper()} ON {table}\n"
        f"      REFERENCING {rows} FOR EACH STATEMENT EXECUTE FUNCTION audit_row_change();\n"
        "  END IF;\n"
        for write, rows in AUDITED_WRITES
    )
    return f"DO $app$\nBEGIN\n{creates}END\n$app$"


SCHEMA.extend(
    (f"{POLICY_BAND + i:04d}_{t}_policy", _policy_sql(t)) for i, t in enumerate(TENANT_TABLES)
)
SCHEMA.extend(
    (f"{FORCE_BAND + i:04d}_{t}_force", _force_sql(t)) for i, t in enumerate(TENANT_TABLES)
)
SCHEMA.extend(
    (f"{AUDIT_BAND + i:04d}_{t}_audit", audit_sql(t))
    for i, t in enumerate(TENANT_TABLES)
    if t != APPEND_ONLY_TABLE
)
"""Each table is created, then given its policy, then forced, then audited, in key order.
`audit_events` is not audited."""

SCHEMA.extend(
    [
        (
            "0200_audit_events_outcome",
            "ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS outcome text NOT NULL DEFAULT 'done'"
            " CONSTRAINT audit_events_outcome_known CHECK (outcome IN ('done', 'absent'))",
        ),
        (
            "0201_audit_events_method",
            "ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS method text",
        ),
        (
            "0202_audit_events_route",
            "ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS route text",
        ),
        (
            "0203_audit_events_status",
            "ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS status smallint",
        ),
        (
            "0204_audit_events_action_optional",
            "ALTER TABLE audit_events ALTER COLUMN action DROP NOT NULL",
        ),
        (
            "0205_audit_events_outcome_optional",
            "ALTER TABLE audit_events ALTER COLUMN outcome DROP NOT NULL",
        ),
        (
            "0206_audit_events_outcome_no_default",
            "ALTER TABLE audit_events ALTER COLUMN outcome DROP DEFAULT",
        ),
        (
            "0207_audit_events_resource_type_optional",
            "ALTER TABLE audit_events ALTER COLUMN resource_type DROP NOT NULL",
        ),
        (
            "0208_audit_events_kind_whole",
            "DO $app$\nBEGIN\n"
            "  IF NOT EXISTS (SELECT 1 FROM pg_constraint\n"
            "                 WHERE conrelid = 'audit_events'::regclass\n"
            "                   AND conname = 'audit_events_kind_whole') THEN\n"
            "    ALTER TABLE audit_events ADD CONSTRAINT audit_events_kind_whole CHECK (\n"
            "      (method IS NULL) = (status IS NULL)\n"
            "      AND (method IS NULL) = (route IS NULL)\n"
            "      AND (action IS NULL) = (outcome IS NULL)\n"
            "      AND (action IS NULL) = (resource_type IS NULL)\n"
            "      AND (method IS NULL) = (action IS NOT NULL)\n"
            "    );\n"
            "  END IF;\nEND\n$app$",
        ),
    ]
)
"""The repairs, keyed above the policies so the list stays in the order it is applied."""
