# The application never applies DDL, and it refuses a schema that is not exactly its own

**Amended 2026-08-27.** This record absorbed the schema-version match, which stood as a record
of its own until the two were merged. Neither claim was decided differently; the rejected
options and the costs of both are below, and the title now states both halves.

**Amended 2026-08-30.** *The match is exact, in both directions* below said the comparison
was the version marker, `max(key)`. It is now the set of applied keys, and what forced the
change is recorded in that section. The claim it makes is unchanged and slightly stronger. The
version marker still exists, and nothing decides on it.

Three consequences moved with it. `make migrate` no longer "exits 0 when already current"; it
re-runs every entry. The refusal names the entries the database has not applied rather than two
version strings. And two consequences are new: the schema baseline, and what it costs.

The running application holds no rights to change the schema and no credential that could. It
verifies at startup and refuses to serve unless the entries applied to the database are exactly
the entries this build carries. Applying them is `make migrate` — a release step, run by
something that is not the web process.

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

## The match is exact, in both directions

`check` refuses a database **missing** any entry this build carries, and one **carrying** an
entry this build does not. `apply` refuses the second too.

Missing is the obvious half: the columns this build names may not exist yet, and the release
step was skipped.

An unknown entry is the half worth writing down, because it could reasonably go the other way.
It means a newer release has already migrated this database. Every migration here is
additive — `tests/store/test_schema.py::test_every_entry_is_additive` refuses `DROP TABLE`,
`DROP COLUMN`, `ALTER COLUMN ... TYPE` and `RENAME` — so an older build *can* still write
everything it knows about, and serving it would usually work.

**Usually is the problem.** Tolerating it is a compatibility judgement, made at startup, by a
process that has no way to check whether it is true. It is true while migrations stay additive
and stops being true the first time somebody needs an exception; and when it stops being true,
the failure is an old build writing rows to a shape it does not understand, which reports
itself as a successful write. The application refuses that class of thing everywhere else, and
there is no reason for the schema check to be where it starts guessing.

**The comparison is the set of applied keys, and was `max(key)` until 2026-08-30.** A single
highest key cannot see an entry added below it. Bands were the mitigation: repairs sat at
`0200_` so they always sorted above, and a repair keyed anywhere else was applied to no
database and reported by nothing. That is a rule a person has to remember, guarding a failure
that is silent, which is the shape this project refuses everywhere else. Comparing the set
removes the failure instead of detecting it, and `apply` now runs every entry on every release
step rather than returning early on a marker that has not moved. Keys still decide the order
an entry runs in. They no longer decide whether it runs at all.

Matching on the set makes the rule the same in both directions and in both functions: one
sentence, and no window in which two schemas are both correct.

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

Two more options were rejected on the version match rather than on who applies it.

**Warning on ahead and serving anyway**, so that a rolling deploy never crash-loops an
instance of the previous release. This was the original decision here and it was reversed
deliberately, with the cost below understood. The argument for it is real — the window is
short and the additive rule does make it safe today — and it was rejected because "safe today,
by a rule that lives in another file, checked by nobody at the moment it matters" is not the
shape of guarantee this project makes elsewhere.

**Tolerating a bounded number of entries ahead.** Rejected: a knob that encodes a guess about
how long a rollout takes, which nobody would tune and everybody would eventually trip over.

## Consequences

**`make migrate` is a release step and has to be wired up.** Fly's `release_command`, a
pre-deploy command on Railway or Render, a `pre-upgrade` Job on Kubernetes, a one-off task on
ECS, the `migrate` service in `deploy/compose.yaml`. It is idempotent, serialises on an
advisory lock so two releases cannot race, and re-runs every entry harmlessly, which is what
makes it safe in a hook that fires more than once.

**Forgetting it fails the deploy rather than corrupting anything.** The new version refuses to
start, naming the entries the database has not applied. That refusal is the enforcement.

**A rolling deploy has a window.** Between the release step and the last old instance being
replaced, the database is ahead of every instance still running the previous version. Those
instances keep serving — they checked at boot and do not re-check — but any of them that
*restarts* in that window will refuse to come up: a health-check failure, a node eviction, an
autoscaler. The window is the length of the rollout. Make the migration and the rollout one
step — scale down, migrate, scale up — or accept the window, which for most deployments is a
minute and a risk nobody notices. What you should *not* do is quietly soften the check; change
this decision on purpose instead.

**Rolling the application back requires rolling the schema back.** Redeploying the previous
version against a migrated database will not start. Plan a rollback as a schema rollback, or
as rolling forward to a fixed build.

**Migrations stay additive anyway**, and the test stays. Additive migrations are what make an
expand-and-contract change possible across two releases, and what keeps a half-finished deploy
from leaving the database in a shape nothing can read.

**Editing or removing a shipped entry is refused by the gate, not at deploy.** Comparing the
set of applied keys cannot see either: an edited body leaves every key matching, and a deleted
key is only visible once a database that ran it is in front of you. `backend/.schema-baseline.json`
records a hash per entry and refuses both in the pre-commit hook, on `.complexity-baseline.json`'s
terms — regenerating cannot quiet it, so the only way past is a line in a diff somebody reads.

A cosmetic edit stops a deploy too, and that is accepted: nothing can tell a reformat from a
column, and the failure being prevented is silent.

**Flyway and Liquibase do the same check in the database, and this one deviates on purpose.**
Both store a hash per applied migration and validate it at deploy; Flyway's `repair` is the
escape hatch our hand-edited line is. A runtime version was built here first and rejected. The
mistake is visible at commit time, so a check at deploy is the latest moment it can be caught
rather than the earliest, and putting it in `applied_once` would mean reshaping the one table
the first migration creates. The cost of deviating is that the gate only sees a working tree: a
build that bypassed it is not caught later. Alembic makes neither choice and hashes nothing, so
it does not catch this at all — `docs/schema.md` says what that means for anyone leaving.

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
