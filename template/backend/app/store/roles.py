"""The two database roles, and the grants that make least privilege real.

A **database role** is the Postgres identity a connection authenticates as. This project uses
two, and the split is the whole reason the application never applies DDL:

- **`<schema>_owner`** owns the schema and every table in it, and applies the DDL. `NOLOGIN`:
  nothing serves traffic as it, and `make migrate` reaches it by connecting as a role that is
  a *member* and issuing `SET ROLE`.
- **`<schema>_app`** is what the application connects as. DML only, never `CREATE`, and
  **never `BYPASSRLS`** — it is subject to every policy in `ddl.py`. It may read the migration
  ledger and not write it, so the role that verifies the schema cannot forge its own answer. It
  may insert into `audit_events` and select from it, and never update or delete an event or set
  its time or its sequence.

There is deliberately **no third role**, and no `SECURITY DEFINER` function anywhere. A role
holding `BYPASSRLS` exists only to answer a question that cannot know a tenant — a queue claim
taking the next row across tenants is the usual one — and this project has no such question.
`tests/integration/test_isolation.py` asserts the absence rather than trusting it, because
"nothing here can see across tenants" is exactly the claim a security review asks you to prove.

Role names derive from the schema because roles are **cluster-wide**, not per-database. Two
projects generated from this template that both hard-coded `app_owner` would collide the
moment they shared a cluster, and the second one to migrate would silently inherit the first
one's grants.

This module emits SQL and never runs it. Provisioning roles needs `CREATEROLE`, which this
project does not ask for and a generated application has no business assuming: an operator
applies `deploy/roles.sql` once, before the first migration. `make roles` regenerates it and
`tests/store/test_schema.py` asserts the committed file is still what this module emits.
"""

from app.store.conn import quote_ident, resolve_schema

_DML = "SELECT, INSERT, UPDATE, DELETE"

APPEND_ONLY_TABLE = "audit_events"
"""The table the application role may select from and insert into, and never update or delete
from."""

FILLED_BY_THE_DATABASE = ("seq", "recorded_at")
"""The columns of `APPEND_ONLY_TABLE` the application role may not insert a value into."""

LEDGER_TABLE = "applied_once"


def _guarded(table: str, role: str, body: str, declare: str = "") -> str:
    return (
        f"DO $app$\n{declare}BEGIN\n"
        f"  IF to_regclass('{table}') IS NOT NULL\n"
        f"     AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN\n"
        f"{body}"
        "  END IF;\nEND\n$app$"
    )


def writes_withheld_sql(schema: str | None = None) -> list[str]:
    """What every migration, `deploy/schema.sql` and `deploy/roles.sql` end with: the application
    role holds no write on the ledger, and on `APPEND_ONLY_TABLE` only an insert into the columns
    but `FILLED_BY_THE_DATABASE`. Each statement does nothing while its table or the role does
    not exist, and running it again changes nothing. `docs/adr/template/0003` and `0017`."""
    name = quote_ident(resolve_schema(schema))
    app = app_role(schema)
    ledger = f"{name}.{LEDGER_TABLE}"
    events = f"{name}.{APPEND_ONLY_TABLE}"
    filled = ", ".join(f"'{column}'" for column in FILLED_BY_THE_DATABASE)
    revoke = "    EXECUTE format('REVOKE INSERT, UPDATE, DELETE ON {table} FROM %I', '{role}');\n"
    return [
        _guarded(ledger, app, revoke.format(table=ledger, role=app)),
        _guarded(
            events,
            app,
            revoke.format(table=events, role=app) + "    FOR writable IN\n"
            "      SELECT attname FROM pg_attribute\n"
            f"      WHERE attrelid = '{events}'::regclass AND attnum > 0 AND NOT attisdropped\n"
            f"        AND attname NOT IN ({filled})\n"
            "    LOOP\n"
            f"      EXECUTE format('GRANT INSERT (%I) ON {events} TO %I', writable, '{app}');\n"
            "    END LOOP;\n",
            "DECLARE\n  writable name;\n",
        ),
    ]


def owner_role(schema: str | None = None) -> str:
    """The role that owns the schema and applies the DDL."""
    return f"{resolve_schema(schema)}_owner"


def app_role(schema: str | None = None) -> str:
    """The role the application connects as."""
    return f"{resolve_schema(schema)}_app"


def _create_role(role: str, attributes: str) -> str:
    """`CREATE ROLE` guarded on the catalog — Postgres has no `IF NOT EXISTS` for a role."""
    return (
        "DO $app$\nBEGIN\n"
        f"  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN\n"
        f"    CREATE ROLE {role} {attributes};\n"
        "  END IF;\nEND\n$app$;"
    )


def emit_roles_sql(schema: str | None = None) -> str:
    """The roles and grants, as a script an operator applies once.

    Safe to re-apply. Apply it **before** the first migration: it creates the schema owned by
    `<schema>_owner` so that `ALTER DEFAULT PRIVILEGES` is in place before the first table
    exists. Run it afterwards instead and the next migration's table is unreadable by the
    application until somebody re-runs a `GRANT` nobody remembers.

    No credentials appear here, and none should be added. A password vendored into a script is
    a password in version control; set it out of band.
    """
    name = quote_ident(resolve_schema(schema))
    owner = owner_role(schema)
    app = app_role(schema)
    return "\n".join(
        [
            f"-- Database roles and grants for the {name} schema. Generated by `make roles`.",
            "-- Apply as a role holding CREATEROLE, once, before the first `make migrate`.",
            "-- Safe to re-apply, in one transaction, so no write is granted between statements.",
            "-- Set the password for the application role out of band.",
            "BEGIN;",
            "",
            f"-- {owner}: owns the schema, applies the DDL, serves nothing.",
            _create_role(owner, "NOLOGIN"),
            "",
            f"-- {app}: what the application connects as. DML only, subject to every policy.",
            _create_role(app, "LOGIN"),
            "",
            "-- The schema, owned so that the default privileges below actually bind.",
            f"CREATE SCHEMA IF NOT EXISTS {name} AUTHORIZATION {owner};",
            f"GRANT USAGE ON SCHEMA {name} TO {app};",
            "",
            "-- Existing objects, for a schema that was migrated before these roles existed.",
            f"GRANT {_DML} ON ALL TABLES IN SCHEMA {name} TO {app};",
            f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {name} TO {app};",
            "-- Except writes on the ledger, and all but inserts on the audit events.",
            *(f"{statement};" for statement in writes_withheld_sql(schema)),
            "",
            f"-- Everything {owner} creates from here on. Without this, the table added by the",
            "-- next migration is invisible to the application until somebody re-runs the GRANT.",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA {name}",
            f"  GRANT {_DML} ON TABLES TO {app};",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA {name}",
            f"  GRANT USAGE, SELECT ON SEQUENCES TO {app};",
            "",
            "-- Every migration withholds the same writes, because this script, run before the",
            "-- first one, finds no table to withhold them on. See app/store/migrate.py.",
            "COMMIT;",
            "",
        ]
    )
