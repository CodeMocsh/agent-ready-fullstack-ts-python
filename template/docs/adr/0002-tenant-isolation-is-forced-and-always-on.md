# Tenant isolation is forced, and always on

Every table holding tenant data has row-level security **enabled and forced**, with one policy
admitting rows whose `tenant_id` matches `current_setting('app.tenant_id', true)` — for reads
and writes alike. There is no switch.

Filtering by tenant in application code is a promise. "Every query remembered the filter" is
not a claim anyone can verify by reading, and it is exactly the claim a security review asks
you to prove. A policy in the database is a mechanism instead: a forgotten `WHERE`, a
hand-written query in a new module, a `SELECT *` in a debugging endpoint — none of them can
cross a tenant boundary.

**`FORCE` rather than plain `ENABLE` is not hardening; it is the only thing that works.** A
table's owner bypasses its own policies by default, and on a database where an ordinary role
applied the schema that owner is also the role running the queries. Verified against
PostgreSQL 17: with `ENABLE` alone and no tenant set, the owner reads every row; with `FORCE`,
it reads none.
`tests/integration/test_isolation.py::test_the_owner_is_subject_to_its_own_policy` is that
verification, and it fails the moment `FORCE` is dropped.

**Every policy is created before any table is forced.** The other order enforces a table
during the window before its own policy exists, and every statement against it fails.

**`WITH CHECK` is written out even though it is redundant.** With `FOR ALL` and a `USING`
clause, Postgres applies `USING` to new rows too — removing the line changes nothing, which is
not a guess: it was removed, and the whole isolation suite still passed. It is here because it
stops being redundant the moment the policy is split per command or `USING` is narrowed, and a
policy whose two expressions disagree is exactly where a tenant inserts a row it cannot then
read — a corruption that reports itself as success.

**And no row may carry an empty tenant.** `current_setting(..., true)` reads as NULL when
never set and as the empty string after a connection pool issues `RESET ALL`. Neither matches
any row — the first because nothing equals NULL, the second only because
`tasks_tenant_id_not_empty` refuses to store one. Without that constraint a single row with an
empty `tenant_id` would be readable by every connection that had not set a tenant: a
permanent cross-tenant read caused by a data bug rather than a policy bug.

## Considered options

**Making isolation switchable and defaulting it off**, on the grounds that a single-tenant
project gains nothing from a policy. Rejected: the failure mode here is **silence** — a
mis-set policy is indistinguishable from a correct one until a second tenant exists — and a
switch guarantees that the configuration everybody runs is the one nobody exercises. Isolation
costs one indexable predicate against a constant, so a single-tenant deployment pays
approximately nothing to leave it on.

**Shipping the column with the policies off**, to be turned on later. Rejected as the worst of
both: you pay for the column, get no mechanism, and the switch-on is the untested path.

**Scoping the policy `TO <schema>_app`.** Rejected: with one application role there is nothing
for the planner to skip, and naming a role would drag role names into the migration and make
it require them to exist.

## Consequences

**Every index on a tenant table must lead with `tenant_id`**, and
`tests/store/test_schema.py::test_every_created_index_leads_with_the_tenant` enforces it. An index
that does not cannot satisfy the policy's predicate, and Postgres falls back to scanning far
more rows than it should — correct answers, quietly slower, invisible until the table is large
enough that fixing it means rebuilding indexes on live data. Constraint-backed indexes are
exempt: a primary key on a uuid and the `UNIQUE (id, tenant_id)` a child table's foreign key
must reference are about uniqueness of a surrogate key, not about serving a scan.

**A policy may only compare a column to a setting.** Anything that needs a join or a
row-dependent function call is a bug in the schema rather than a policy to write: those are
the policies that turn a 12 ms query into a 178-second one, because the planner runs them per
row instead of once.

**A superuser bypasses every policy, and nothing here can prevent it.** The guarantee is that
a forgotten filter cannot cross a tenant — not that a privileged connection cannot. This is
why the test suite connects as `<schema>_app` and never as the administrator: a suite wired to
the admin connection would pass against a database with no policies at all.

**The ledger is not tenant data** and carries no policy, because the schema version is nobody's
data and a policy on it would hide it from the role whose only job is to read it.
