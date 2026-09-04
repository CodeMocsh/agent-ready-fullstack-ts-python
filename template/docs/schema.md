# The schema

This project ships its own small migration system rather than Alembic. This page says what it
is, what it deliberately will not do, and how to leave when you outgrow it.

## What it is

`backend/app/store/ddl.py` holds the schema as **data**: a list of entries, each one a key and
a single SQL statement, every one idempotent and additive.

- **`make migrate`** applies every entry, in key order, on every run, under one advisory lock.
  There is no "already current" shortcut, so an entry keyed anywhere runs.
- **`applied_once`** records the key of each entry that ran. Once the release step has run
  against a database that has its roles, the application role holds `SELECT` on that table and
  nothing else, so the process that verifies the schema cannot forge its own answer.
- **The application never applies DDL.** It compares the keys in `applied_once` against the keys
  it carries, and refuses to serve when they differ in either direction —
  [adr/0003](adr/0003-the-application-never-applies-ddl.md).
- **`deploy/schema.sql`** is the same statements as a script, for a deployment whose own tooling
  owns the DDL. Regenerate with `make schema`.
- **`backend/.schema-baseline.json`** records a hash of each entry body. Editing or removing an
  entry that already shipped fails `make test`, in the pre-commit hook.

## What it deliberately does not do

- **No down migrations.** Roll forward, or plan a rollback as a schema rollback.
- **No branches or merges.** One ordered list, and two developers adding a key in the same band
  resolve it the way they resolve any other conflict in a list.
- **No data migrations.** A backfill is a script you write and run once. Nothing tracks it.
- **No autogenerate.** There is no ORM here, so nothing can diff models against a database. You
  write the SQL.
- **No hash in the database.** Flyway and Liquibase store one per applied migration and validate
  it at deploy. This project checks the same thing at commit time instead, in
  `.schema-baseline.json`, which fires earlier and needs no database — and which only sees your
  working tree. That trade is recorded in
  [adr/0003](adr/0003-the-application-never-applies-ddl.md).

## Why it is this small

Because the schema is data, the fast tier asserts properties of it directly, in milliseconds,
with no Postgres anywhere. That a tenant table cannot arrive without a policy. That an index on
one leads with `tenant_id`, or the policy cannot use it. That the column and the `CHECK` an unset
tenant relies on are both there. That no entry is destructive, so an older instance keeps serving
through a rollout. Read `backend/tests/store/test_schema.py` for what is asserted today.

Those are what make "tenant isolation is forced and always on"
([adr/0002](adr/0002-tenant-isolation-is-forced-and-always-on.md)) a mechanism rather than a
claim. They are cheap because a list is cheap to read.

## When to leave

Move to Alembic when any of these is true:

- you need down migrations, or branching, or backfills tracked as first-class;
- you adopt SQLAlchemy, at which point `--autogenerate` and `alembic check` start earning their
  keep;
- the schema is large enough that one ordered list is harder to read than a directory of files.

Know what you give up. **Alembic does not hash migration bodies** — it trusts the revision
graph, so a migration edited after it ran is applied to no database and reported by nothing.
That is the failure `.schema-baseline.json` exists to refuse. Flyway and Liquibase do hash, and
are the alternatives to weigh if that guarantee matters to you.

The assertions above go with it, too. A schema spread across `versions/*.py` cannot be read as a
list, so those checks either move to the integration tier, which needs a running Postgres and
does not run by default, or they stop being made.

## How to leave

1. Add `alembic` and point `env.py` at `DATABASE_OWNER_URL`, never `DATABASE_URL`. The
   application role still holds no `CREATE`.
2. Make one initial revision whose `upgrade()` runs the statements in `deploy/schema.sql`.
3. On every database that already exists, `alembic stamp head` so the revision is recorded
   without re-applying it.
4. Delete `app/migrate.py`, `backend/.schema-baseline.json` and the `migrate` and `schema`
   targets in the `Makefile`. Keep `app/store/roles.py`: the two roles are independent of who
   applies the DDL.
5. Rewrite `app/store/migrate.py` down to whatever `Database.check()` and `MemoryDatabase` still
   need. Both import from it today, so deleting the module outright breaks the memory substrate.
   `check` becomes a read of `alembic_version`, or goes, and a deploy-order mistake then surfaces
   as a query error.
6. Decide, out loud, what happens to the assertions above.
