# The application never applies DDL

The running application holds no rights to change the schema and no credential that could. It
verifies at startup and refuses to serve if the schema is behind. Applying it is `make
migrate` — a release step, run by something that is not the web process.

Two database roles carry this. `<schema>_owner` owns the schema and every table and is
`NOLOGIN`, so nothing serves traffic as it. `<schema>_app` is what the application connects
as: DML only, never `CREATE`, never `BYPASSRLS`, and no write access to the migration ledger,
so the role that verifies the schema cannot forge its own answer.

**The forcing reason is a privilege check, not taste.** PostgreSQL tests permission *before*
existence, so `CREATE TABLE IF NOT EXISTS` against an already-correct table fails with
`permission denied for schema` for a role holding no `CREATE`. A least-privilege application
could not start at all without a path that only reads — so the split is not something the
verify path enables, it is something it requires.

**And an application that could apply DDL would be an application whose compromise can drop
your tables.** `DATABASE_OWNER_URL` is read by `python -m app.migrate` and nowhere else, and
`wiring.build()` **refuses to start** if it can see that variable — the same argument as
`FORCE` over `ENABLE`: a separation that depends on nobody making a mistake is not a
separation.

## Considered options

**Migrating on startup, with an owner connection.** This is the convenient shape and it is
what the previous version of this template did. Rejected once the two roles existed: it puts
the credential that can drop the schema into the process most exposed to the internet, and it
makes the configuration that ships to production (`check`) different from the one developers
exercise daily.

**Granting the application role `CREATE`**, which makes the split decorative. Rejected: the
separation is the deliverable.

**A mode flag with `auto` and `check`.** Rejected once the application only ever checked: a
mode with one reachable value is dead configuration that reads like a choice, and keeping it
would mean the application importing the code that applies DDL. It does not — nothing on the
request path can reach `apply`, whatever credential the process was handed.

This is the pattern the field settled on. pg-boss ships `migrate: false` for "when the
configured user account does not have schema mutation privileges"; Graphile Worker ships raw
SQL so the runtime role never needs `CREATE`; River keeps migration in a CLI because "the
application must have elevated access to modify the database schema, and it's generally good
practice to limit the application's database permissions in production."

## Consequences

**`make migrate` is a release step and has to be wired up.** Fly's `release_command`, a
pre-deploy command on Railway or Render, a `pre-upgrade` Job on Kubernetes, a one-off task on
ECS, the `migrate` service in `deploy/compose.yaml`. It is idempotent, serialises on an
advisory lock so two releases cannot race, and exits 0 when already current, which is what
makes it safe in a hook that fires more than once.

**Forgetting it fails the deploy rather than corrupting anything.** The new version refuses to
start, naming both schema versions. That refusal is the enforcement.

**The migration issues `SET ROLE <schema>_owner`, guarded on two questions.** Does the role
exist — roles are cluster-wide, so its existence says nothing about *this* database — and are
we a *member* of it, since an unrelated owner finds the role present and is then refused with
"must be able to SET ROLE". The two tests are nested rather than `AND`-ed, because SQL does not
promise to evaluate the existence test first and `pg_has_role` raises outright on a role that
is not there. Objects created without the `SET ROLE` are owned by whoever connected, fall
outside `ALTER DEFAULT PRIVILEGES FOR ROLE <schema>_owner`, and the application is then refused
at *query* time rather than at migration time.

**Where neither holds, it applies as whoever connected.** That is the bootstrap, not a
fallback: a developer pointing at their own Postgres has no roles provisioned and must still
be able to work, and `FORCE` keeps those objects policy-bound whoever owns them.

**`deploy/roles.sql` must be applied before the first migration.** `ALTER DEFAULT PRIVILEGES`
binds only objects created after it; run it late and the next table is unreadable by the
application until somebody re-runs a `GRANT` nobody remembers.

**Role names derive from `DB_SCHEMA`.** Roles are cluster-wide, so two projects generated from
this template that both hard-coded `app_owner` would collide the moment they shared a cluster,
and the second to migrate would silently inherit the first's grants.
