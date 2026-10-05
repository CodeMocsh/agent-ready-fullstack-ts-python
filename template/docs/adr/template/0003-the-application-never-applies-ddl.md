# 0003. The application never applies DDL, and it refuses a schema that is not exactly its own

Date: 2026-10-05

## Status

Accepted.

## Context

Postgres checks privilege before existence. For a role without `CREATE`,
`CREATE TABLE IF NOT EXISTS` on a table that already exists fails with
`permission denied for schema`. An application with least privilege therefore cannot run DDL at
startup at all. It needs a path that only reads.

An application that can apply DDL can drop the tables when an attacker controls it. The process
most exposed to the internet must not hold that credential. A separation that depends on nobody
making a mistake is not a separation.

When a database carries an entry that this build does not know, a newer release migrated it. Every
migration is additive, so an older build can usually still write. "Usually" is a compatibility
judgement, made at startup, by a process that cannot check it. When the judgement is wrong, an old
build writes rows to a shape it does not know, and the write reports success.

## Decision

- Two database roles carry the split. `app/store/roles.py` emits them into `deploy/roles.sql`.
  - `<schema>_owner` owns the schema and every table, and applies the DDL. It is `NOLOGIN`, so
    nothing serves traffic as it.
  - `<schema>_app` is the role the application connects as. It holds DML only: no `CREATE`, no
    `BYPASSRLS`.
- The application role cannot write the ledger, `applied_once`. `_revoke_ledger` in
  `app/store/migrate.py` revokes the write on every `apply`, because the ledger does not exist
  before the first migration. The role that verifies the schema cannot forge its own answer.
  `test_the_application_role_cannot_rewrite_the_ledger` holds this.
- The release step applies the schema. It is `make migrate`, which runs `python -m app.migrate`,
  and something that is not the web process runs it.
- `app/migrate.py` is the only reader of `DATABASE_OWNER_URL`. `wiring.build()` raises
  `OwnerCredentialVisible` when the application can see that variable.
- `check` and `apply` in `app/store/migrate.py` are two functions, not one function with a mode.
  The lifespan calls `Database.check()` before the first request. Only `app/migrate.py` calls
  `apply`.
- The match is exact, in both directions. `check` raises `SchemaBehindError` when the ledger lacks
  an entry this build carries: the columns this build names may not exist. It raises
  `SchemaTooNewError` when the ledger holds an entry this build does not carry. `apply` raises
  `SchemaTooNewError` too.
- The comparison is the set of applied keys. `_apply_all` runs every entry on every call, under
  an advisory lock, and relies on each entry being idempotent. A key decides the order in which an
  entry runs, never whether it runs.
- `known_version` and `schema_version` report the highest key, for `make migrate` to print. No
  decision reads them.
- Migrations are additive. `tests/store/test_schema.py::test_every_entry_is_additive` refuses
  `DROP TABLE`, `DROP COLUMN`, `ALTER COLUMN ... TYPE` and `RENAME`. Additive entries make an
  expand-and-contract change possible across two releases. They also prevent a half-finished
  deploy from leaving a schema that no build can read.
- A shipped entry is never edited or removed. `backend/.schema-baseline.json` records a hash of
  each entry body, and `test_no_shipped_entry_body_has_changed` refuses an edit or a removal in
  the gate. `merged_baseline` in `backend/devtools/schema.py` keeps the recorded hash and refuses
  a removed key, so `make schema` cannot hide the change. The only way past is a hand edit to the
  baseline, in a diff that somebody reads.
- `_become_owner` issues `SET ROLE <schema>_owner` when that role exists and the connecting role
  is a member of it. Roles are cluster-wide, so existence alone says nothing about this database.
  `_MAY_SET_ROLE` nests the two tests in `CASE`, because SQL does not promise to evaluate `AND`
  in order, and `pg_has_role` raises on a role that does not exist.
- Where `SET ROLE` is not possible, `apply` runs as the connecting role. This is the bootstrap for
  a developer's own Postgres, which has no roles. `FORCE` keeps those objects bound to the policy
  whoever owns them.
- `deploy/roles.sql` is applied before the first migration. `ALTER DEFAULT PRIVILEGES` binds
  only the objects created after it.
- Role names derive from `DB_SCHEMA`, through `owner_role` and `app_role`. Two projects that
  hard-code `app_owner` collide on a shared cluster, and the second to migrate inherits the
  first's grants.

## Considered options

- **Migrate on startup, with an owner connection.** This puts the credential that can drop the
  schema into the process most exposed to the internet. The configuration that ships to
  production then differs from the one developers run every day. pg-boss, Graphile Worker and
  River all keep migration out of the runtime role for the same reason.
- **Grant the application role `CREATE`.** The split then protects nothing, and the split is the
  deliverable.
- **One function with a mode flag, `auto` or `check`.** Each process can reach only one value, so
  the flag is dead configuration that looks like a choice.
- **Warn when the database is ahead, and serve.** This prevents a crash loop during a rolling
  deploy, and the additive rule makes it safe today. But it rests on a rule in another file that
  nothing checks at the moment it matters.
- **Tolerate a bounded number of unknown entries.** The bound is a guess about how long a rollout
  takes. Nobody tunes it, and every deployment eventually exceeds it.
- **Compare the highest applied key, `max(key)`.** It cannot see an entry keyed below an existing
  one. Every author must then remember where to put a key, and a wrong key fails silently.
- **Check entry hashes at deploy, as Flyway and Liquibase do.** The edit is visible at commit
  time, so a deploy is the latest place to catch it, not the earliest. A deploy check also
  changes `applied_once`, the table the first migration creates. Alembic checks neither.
  [docs/schema.md](../../schema.md) says what that means for a project that moves to it.

## Consequences

- `make migrate` must be wired into the platform's release step. `docs/deployment.md` names the
  hook on each platform. The step is idempotent and holds an advisory lock, so a hook that fires
  more than once is safe.
- A skipped release step fails the deploy and corrupts nothing. The new version refuses to start
  and names the entries the database has not applied.
- A rolling deploy has a window. After the release step, the database is ahead of every instance
  of the previous version. `GET /ready` calls `check`, so a platform that probes it takes those
  instances out of rotation. An instance of the previous version that restarts refuses to start.
  The window lasts until the rollout replaces the last old instance.
- To close the window, make the migration and the rollout one step: scale down, migrate, scale
  up. Do not soften the check. Change this decision instead.
- A rollback of the application needs a rollback of the schema. The previous version refuses to
  start against a migrated database. Roll the schema back, or roll forward to a fixed build.
- A new column is a new repair entry, never an edit to its table's `CREATE`. `ddl.py` states the
  pattern.
- A cosmetic edit to a shipped entry also fails the gate. Nothing can tell a reformat from a new
  column, and the failure it prevents is silent.
- The hash check sees only a working tree. A build that did not pass the gate is not caught at
  deploy.
