# 0017. Every store operation on tenant rows records an audit event, in the same transaction

Date: 2026-10-17

## Status

Accepted.

## Context

A regulated product must say who read or changed which record, and when. HIPAA's audit controls
ask for access as well as change. A review asks for the person, and for the agent that acted for
them.

The log does not answer this. A log line leaves on stdout, a collector may drop it, and nothing
ties it to the change it reports. A trace is sampled. A request line names a route, not the
rows the route returned. A database trigger sees a change but no read, and it sees the
application role, not the person.

Only the store knows both the rows and the actor. Only its transaction can make the change and
its record succeed or fail together.

## Decision

- **A store method takes the actor, and records one audit event for each operation that reads or
  changes rows.** `TaskStore` in `app/store/__init__.py` declares the actor on every method. The
  actor is a parameter rather than fixed at construction, because one piece of work may act as
  several actors through one store. The tenant stays fixed at construction.
- **The event is recorded in the transaction that reads or changes the rows.** When the event
  cannot be recorded, the operation fails and changes nothing.
  `test_a_write_whose_event_cannot_be_recorded_changes_nothing` holds this on Postgres. Both
  substrates build the event before they write anything, so an actor they cannot record
  changes nothing.
- **`app/audit_events.py` builds the event, and the template owns it.** `audit_event` names the
  actor, the action, the resource type, the ids of the rows, the names of the fields written,
  and the request id and sampled trace id from `app/log.py`. A store calls it, and the substrate
  writes what it returns.
- **An event names rows by id and fields by name, and never holds a value a row holds.** A value
  in an audit event is a second copy of the data, with none of its protection.
  `test_an_event_holds_no_value_a_row_holds` holds this.
- **A read records the ids it returned.** A list records one event that names every row in it.
- **An operation that finds no row records the id it looked for, with the outcome `absent`.**
  A row that never existed, one already removed and one in another tenant look the same under
  the policy, so an attempt on another tenant's ids is recorded as well.
  `test_an_operation_on_an_absent_task_records_the_id_it_looked_for` holds this.
- **A request the identity seam refuses records no audit event.** It has no tenant, so it is not
  tenant data, and anybody can send one, so recording each would let anybody fill the table.
  Its `request completed` line names it, with a `401` and a null tenant.
  `test_a_request_the_seam_refuses_is_recorded_only_on_its_request_line` holds this.
- **An actor is recorded as a snapshot**: its issuer, subject and name at the time, never a
  reference to a row. A person who is renamed or removed does not change what an event says.
  `on_behalf_of` is recorded one level deep. `audit_event` raises `UnrecordableActor` for a
  deeper chain, because dropping a level would forget a person.
- **`audit_events` is tenant data, and append-only.** It is in `TENANT_TABLES`, so its policy is
  forced like every other. The application role holds `SELECT`, and no `UPDATE` or `DELETE`, so
  it cannot change or remove an event. It holds `INSERT` on every column but `seq` and
  `recorded_at`, which the database fills, so it cannot backdate or reorder one.
  `writes_withheld_sql` in `app/store/roles.py` sets this, after the default privileges grant
  `SELECT`, `INSERT`, `UPDATE` and `DELETE`. `apply` runs it after the last entry,
  `deploy/schema.sql` ends with it, and `deploy/roles.sql` ends with it after its own grants.
  `test_the_application_role_cannot_change_remove_or_backdate_an_event`,
  `test_applying_the_roles_again_grants_no_forbidden_write_on_an_event` and
  `test_the_schema_script_withholds_the_same_writes_as_a_migration` hold each.
- **Every event belongs to exactly one tenant.** `tenant_id` on `audit_events` is `NOT NULL` and
  never empty, as on every tenant table, and a recorded event names it.
- **Work outside a request is recorded under the actor its caller passes.** A job or an agent is
  an actor too. Its `request_id` and `trace_id` are `None`.
- **`Database.audit_events(tenant_id)` reads one tenant's events back, in the order they were
  recorded.** The store contract in `tests/store_contract.py` holds both substrates to every rule
  above that a substrate can show.

## Considered options

- **Audit events in the log, or in traces.** Both are lossy by design, and neither shares a
  transaction with the change.
- **A database trigger, or `pgaudit`.** Each sees changes and statements, not the person or the
  agent, and a trigger sees no read. `pgaudit` is the right record of who reached the database
  outside the application. It is a deployment's to run, beside this.
- **One event per request, written by middleware.** It names a route, not rows. It runs after
  the handler, outside the transaction, so a change can commit and its record be lost.
- **The actor fixed when the store is made.** One piece of work that acts for a person and then
  for itself would need two stores, and the second is easy to forget.
- **An event with the row's values.** That is a copy of the data in a table with weaker
  controls. A project that needs earlier values keeps them in its own tables, under their own
  policy.
- **The grant as an entry in `ddl.py`.** It has to run after every repair, so its key would be
  the highest for good, and the schema version a person reads would stop changing.

## Consequences

- Every store method a project adds takes the actor and records its event, and its contract
  tests assert the event the way `tests/store_contract.py` asserts the example's.
- A read costs one insert. A list costs one insert, whatever its length.
- The application can insert an event that did not happen. It cannot choose when the event
  happened, or where it sorts. The events prove what the application recorded, not more. A
  deployment that needs evidence against the application itself ships the table to storage
  the application cannot write, and runs `pgaudit` beside it.
- A failed sign-in is the identity provider's record. A credential the seam rejects, because it
  is expired, forged or for another audience, is recorded only on its `request completed`
  line, which is lossy and does not say why.
- A project that refuses a signed-in actor inside a tenant, for a role it lacks, records an
  event for that refusal where it refuses. A new outcome needs a repair that replaces the
  `audit_events_outcome_known` constraint.
- Reading the audit events records no event. No route serves them. A project that serves them
  records the read where it serves them.
- An action on a tenant itself is recorded in that tenant, and an action across tenants records
  one event in each. The template has neither; a project that adds one keeps this rule.
- Retention, export and review of the audit events are the deployment's. Nothing here deletes
  one.
- `user` names no concept here: an actor may be an app. The request line names the actor the
  same way, as `actor.issuer` and `actor.subject`
  ([0011](0011-every-log-line-is-declared-and-written-as-json-to-stdout.md)).
