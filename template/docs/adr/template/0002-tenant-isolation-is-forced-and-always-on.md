# 0002. Tenant isolation is forced, and always on

Date: 2026-10-05

## Status

Accepted.

## Context

A tenant filter in application code is a promise. Nobody can verify "every query remembered the
filter" by reading the code, and a security review asks for exactly that proof. A forgotten
`WHERE`, a hand-written query in a new module, or a `SELECT *` in a debug endpoint crosses a
tenant boundary.

A policy in the database is a mechanism. No query can cross the boundary that it draws.

A table's owner bypasses its own policies by default. Where an ordinary role applied the schema,
the owner is also the role that runs the queries. With `ENABLE` alone and no tenant set, the owner
reads every row, and nothing reports it.

A wrong policy looks the same as a correct one until a second tenant exists. A failure here is
silent.

## Decision

- Every table that holds tenant data has row-level security enabled and forced. There is no
  switch. `_policy_sql` and `_force_sql` in `app/store/ddl.py` emit both for each table in
  `TENANT_TABLES`.
- One `FOR ALL` policy per table admits rows whose `tenant_id` equals
  `current_setting('app.tenant_id', true)`, for reads and for writes.
- `FORCE` is a requirement, not hardening. Without it the policy does not bind the owner.
  `tests/integration/test_isolation.py::test_the_owner_is_subject_to_its_own_policy` fails when
  `FORCE` is removed.
- Every policy is created before any table is forced. The keys in `ddl.py` set this order. In
  the other order, a table is enforced before its policy exists, and every statement on it fails.
- `WITH CHECK` is written out, though it is redundant today. With `FOR ALL` and `USING`, Postgres
  applies `USING` to new rows too, and the isolation suite passes without the line. It stops
  being redundant when somebody splits the policy per command or narrows `USING`. Then a tenant
  can insert a row that it cannot read, and the insert reports success.
- No row may carry an empty tenant. `current_setting(..., true)` reads NULL when the setting is
  unset, and the empty string after a pool issues `RESET ALL`. NULL matches no row. The empty
  string matches no row only because the `tasks_tenant_id_not_empty` constraint refuses to store
  one. Without that constraint, one row with an empty `tenant_id` is readable by every connection
  that did not set a tenant. `test_an_empty_tenant_is_not_a_wildcard` and
  `test_an_empty_tenant_cannot_be_stored` hold this.
- The store sets the tenant for one transaction only, with `set_config(..., true)` in
  `PostgresTaskStore._scoped` in `app/store/pg.py`. A session setting outlives its transaction,
  and the next request on that pooled connection inherits the tenant.
  `test_the_tenant_does_not_survive_its_transaction` holds this.
- `store()` raises `TenantUnset` on an empty tenant, on both substrates. Under the policy an
  unset tenant matches no rows, and no rows looks like a true answer.
- Every table is in `TENANT_TABLES` or in `EXEMPT_FROM_ISOLATION`.
  `tests/store/test_schema.py::test_every_table_is_isolated_or_explicitly_exempt` fails on a table
  in neither. Such a table gets no policy and no `FORCE`, and every tenant can read it.
- The migration ledger, `applied_once`, is the one exemption. The schema version is nobody's
  data. A policy on it hides it from the role whose only job is to read it.
- No role that the application uses holds `BYPASSRLS`, and the schema has no `SECURITY DEFINER`
  function. `test_the_application_role_holds_no_bypass` and
  `test_no_security_definer_function_exists_in_the_schema` assert the absence.

## Considered options

- **Isolation as a switch, off by default.** A single-tenant project gains nothing from a policy.
  But a switch makes sure that the configuration everybody runs is the one that nobody tests.
  Isolation costs one indexable predicate against a constant, so a single-tenant deployment pays
  almost nothing.
- **The column now, the policies later.** This costs the column and gives no mechanism. Turning
  the policies on later is the path that nobody has tested.
- **A policy scoped `TO <schema>_app`.** With one application role, the planner has nothing to
  skip. A role name in the policy puts role names into the migration, and the migration then
  requires the roles to exist.

## Consequences

- Every index on a tenant table leads with `tenant_id`.
  `tests/store/test_schema.py::test_every_created_index_leads_with_the_tenant` enforces it. An
  index that does not lead with it cannot serve the policy's predicate, so Postgres scans too
  many rows. The answers stay correct and get slower. The problem shows only when the table is
  large, and then the fix rebuilds indexes on live data.
- Indexes that back a constraint are exempt from that rule. A uuid primary key and the
  `UNIQUE (id, tenant_id)` that a child table's foreign key references exist for uniqueness, not
  to serve a scan.
- A policy may only compare a column to a setting. A join or a function call per row in a policy
  is a schema bug. The planner then runs the policy once per row instead of once per query. That
  can turn a 12 ms query into a 178-second one.
- Anything finer than the tenant is enforced in code, beside a route.
  [0004](0004-a-route-cannot-escape-the-identity-seam.md) says where.
- A question that must read across tenants, such as a queue claim, has no path here. It needs a
  new decision.
- A superuser bypasses every policy, and nothing here prevents it. The guarantee is that a
  forgotten filter cannot cross a tenant boundary, not that a privileged connection cannot.
- So the test fixtures in `tests/integration/conftest.py` connect as `<schema>_app`, never as the
  administrator. A suite on the administrator connection passes against a database with no
  policies at all.
