# The schema and the binary must match

`check` refuses a database that is **behind** this build and one that is **ahead**. `apply`
refuses ahead too. The version marker must match exactly, or the process does not start.

Behind is the obvious half: the columns this build names may not exist yet, and the release
step was skipped.

Ahead is the half worth writing down, because it could reasonably go the other way. A newer
schema means a newer release has already migrated this database. Every migration here is
additive — `tests/store/test_schema.py::test_every_entry_is_additive` refuses `DROP TABLE`,
`DROP COLUMN`, `ALTER COLUMN ... TYPE` and `RENAME` — so an older build *can* still write
everything it knows about, and serving it would usually work.

**Usually is the problem.** Tolerating ahead is a compatibility judgement, made at startup, by
a process that has no way to check whether it is true. It is true while migrations stay
additive and stops being true the first time someone needs an exception; and when it stops
being true, the failure is an old build writing rows to a shape it does not understand, which
reports itself as a successful write. This codebase refuses that class of thing everywhere
else, and there is no reason for the schema check to be where it starts guessing.

Exact matching also makes the rule the same in both directions and in both functions: one
sentence, no window in which two versions are both correct.

## Considered options

**Warning on ahead and serving anyway**, so that a rolling deploy never crash-loops an
instance of the previous release. This was the original decision here and it was reversed
deliberately, with the cost below understood. The argument for it is real — the window is
short and the additive rule does make it safe today — and it was rejected because "safe
today, by a rule that lives in another file, checked by nobody at the moment it matters" is
not the shape of guarantee this project makes elsewhere.

**Tolerating a bounded number of entries ahead.** Rejected: a knob that encodes a guess about
how long a rollout takes, which nobody would tune and everybody would eventually trip over.

## Consequences

**A rolling deploy has a window.** Between the release step and the last old instance being
replaced, the database is ahead of every instance still running the previous version. Those
instances keep serving — they checked at boot and do not re-check — but any of them that
*restarts* in that window will refuse to come up: a health-check failure, a node eviction, an
autoscaler. The window is the length of the rollout.

If that matters for your deployment, the options are to make the migration and the rollout
one step rather than two — scale down, migrate, scale up — or to accept the window, which for
most deployments is a minute and a risk nobody notices. What you should *not* do is quietly
soften the check; change this decision on purpose instead.

**Rolling the application back requires rolling the schema back.** Redeploying the previous
version against a migrated database will not start. Plan a rollback as a schema rollback, or
as rolling forward to a fixed build.

**Migrations stay additive anyway**, and the test stays. The reason changed rather than
disappeared: additive migrations are what make an expand-and-contract change possible across
two releases, and what keeps a half-finished deploy from leaving the database in a shape
nothing can read. They are no longer load-bearing for *this* decision, which is now the
stricter one.
