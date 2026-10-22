# 0017. The database records every write, and the request layer records every request

Date: 2026-10-21

## Status

Accepted.

## Context

A regulated product must say who read or changed which record, and when. HIPAA's audit controls
ask for access as well as change. A review asks for the person, and for the agent that acted for
them.

The log does not answer this. A log line leaves on stdout, a collector may drop it, and nothing
ties it to the change it reports. A trace is sampled.

An audit record is worth having only if nothing can forget to write it. A project writes its own
stores and its own routes, and Copier cannot add a line to code the project wrote. A rule that
each store method records its own event holds only for the stores that remember it.

## Decision

- **Two kinds of audit event, in one table.** A row event names one statement's writes to one
  tenant's rows. A request event names one request a principal made. `app/audit_events.py`
  defines both, and the template owns it.
- **Postgres records every write to a tenant table.** `audit_row_change` in `app/store/ddl.py`
  runs from a statement-level trigger for each write `AUDITED_WRITES` names, on each table in
  `TENANT_TABLES`. `audit_sql` creates them, keyed from `AUDIT_BAND`, as the policies and each
  `FORCE` are. A table the project adds to `TENANT_TABLES` gets them on the next
  `make migrate`. `test_every_tenant_table_but_the_audit_events_carries_its_triggers` holds this
  on Postgres, and `test_every_tenant_table_but_the_audit_events_is_audited_once_it_is_forced`
  holds the order without one.
- **A row event is recorded in the transaction that wrote the rows.** When it cannot be recorded,
  the write fails and changes nothing.
  `test_a_write_whose_event_cannot_be_recorded_changes_nothing` holds this.
- **One statement records one row event for each tenant whose rows it wrote.** The event names
  each row by its primary key, with the values of a composite key joined by `:` in key order. It
  names the table, the columns an update changed, and every column on an insert and a delete. A
  statement that writes no row records nothing. A tenant table with no primary key cannot be written.
  `tests/integration/test_row_events.py` holds each rule.
- **`audit_events` is not audited.** Its own inserts would record events without end.
  `test_audited_audit_events_are_reported` holds this.
- **A write with no actor is refused.** The trigger reads the actor from connection settings,
  `ACTING_GUCS`, and raises when no actor issuer or subject is set.
  `test_a_write_with_no_actor_is_refused_and_changes_nothing` holds this.
- **The pool sets the actor on every connection it lends.** `_name_who_acts` in
  `app/store/pg.py` is the pool's `setup`. It sets every one of `ACTING_GUCS` from
  `audit_events.current_actor()` and the request id and trace id in `app/log.py`. It sets each
  one empty when nothing is known. A connection lent again does not carry the actor it had.
  `test_a_connection_lent_again_names_nobody` holds this.
- **The store takes no actor.** No method of `TaskStore` in `app/store/__init__.py` has one.
  Nothing a project writes passes one, so nothing can pass the wrong one or forget to.
- **A request acts as its principal.** `audit_events.serve` names the principal on the request
  line, makes its actor the actor of every row the request writes, and keeps it for the request
  event. `resolved_principal` in `app/deps.py` calls it. A resolver that writes before it has a
  principal, as a first sign-in that binds a subject does, runs that write inside
  `audit_events.acting_as`.
- **Work outside a request runs inside `audit_events.acting_as(actor)`.** A job, a worker loop
  and a script are actors too. An app that acts for a person names the person in
  `on_behalf_of`. Without an actor, the first write fails.
- **`make migrate` acts as `MIGRATION_ACTOR`.** `apply` and `deploy/schema.sql` set
  `MIGRATION_ACTING_SQL` from `app/store/acting.py`. An entry that writes rows is then recorded,
  and not refused. `test_a_row_a_migration_writes_is_recorded_under_the_release_step` holds this
  for `apply`.
- **The request layer records one request event for each request a principal made.**
  `app/request_events.py` records it after the handler, before the response start leaves. It
  names the method, the route template, the values of the path parameters and the status. It
  names no action and no table: a `POST` that starts a run created nothing, and the row events
  name what changed. A refusal, a `404` and a `500` are recorded, because an attempt is what a review
  asks about. `test_each_request_a_principal_made_is_recorded_whatever_it_answered` holds this.
- **When a request event cannot be recorded, the request fails with a `500`**, and the response
  it would have sent does not leave.
  `test_a_request_whose_event_cannot_be_recorded_fails_and_sends_nothing_it_read` holds this.
- **A request with no principal records no audit event.** A request the identity seam refuses
  has no tenant, so it is not tenant data, and anybody can send one. Its `request completed`
  line names it, with a `401` and a null tenant.
  `test_a_request_with_no_principal_records_nothing` holds this. A request cancelled before it
  answers, as at shutdown, records none: it sent no response.
  `test_a_cancelled_request_records_nothing` holds this.
- **`audit_events.serve` refuses to run outside the event loop**, as from a `def` dependency, with
  `ServedOffTheLoop`. An actor set in a worker thread does not reach the route.
  `test_a_principal_served_from_a_worker_thread_is_refused` holds this.
- **An event names rows by id and fields by name, and never holds a value a row holds.** A value
  in an audit event is a second copy of the data, with none of its protection.
  `test_an_event_holds_no_value_a_row_holds` holds this.
- **An actor is recorded as a snapshot**: its issuer, subject and name at the time, never a
  reference to a row. `on_behalf_of` is recorded one level deep. `audit_events.recordable`
  raises `UnrecordableActor` for a deeper chain, because dropping a level would forget a person.
- **`audit_events` is tenant data, and append-only.** It is in `TENANT_TABLES`, so its policy is
  forced like every other. The application role holds `SELECT`, and no `UPDATE` or `DELETE`. It
  holds `INSERT` on every column but `seq` and `recorded_at`, which the database fills.
  `writes_withheld_sql` in `app/store/roles.py` sets this, and `apply`, `deploy/schema.sql` and
  `deploy/roles.sql` each end with it.
  `test_the_application_role_cannot_change_remove_or_backdate_an_event` holds this.
- **`audit_events_kind_whole` keeps the two kinds apart.** A row event has an action, an outcome
  and a resource type. A request event has a method, a route and a status. No event has both.
  `test_an_event_of_both_kinds_is_refused` holds this, on a new database and on one repaired from
  before request events.
- **The in-memory substrate records request events and no row event.** Row events are a
  property of the Postgres schema, as the policy is. Production refuses the in-memory substrate.
  `Database.audit_events(tenant_id)` records a request event and reads both kinds back, in the
  order they were recorded, and `tests/store_contract.py` holds both substrates to it.

## Considered options

- **Each store method takes the actor and records its own event.** It holds only for the stores
  that remember it. A project that replaced the example records nothing.
- **One trigger for each row.** A statement that writes many rows, a chunked upload, would
  record one event for each.
- **The actor set in the transaction that sets the tenant.** A project sets the tenant in each
  store it writes, so the actor would need the same edit in each. The pool's `setup` is one
  place in every project.
- **The request event recorded by the identity dependency.** It runs before the handler, so it
  cannot know the status.
- **An action on a request event, from its method.** Most `POST` routes in a real project are
  actions, not creation, and the request event would say things that did not happen.
- **A recording wrapper around each in-memory store.** It is a second copy of the trigger, and
  each store a project adds must remember to use it.
- **The read rows' ids on a request event.** A list does not say which rows it returned. A
  project that must name them records them where it reads them.
- **Audit events in the log, or in traces.** Both are lossy by design, and neither shares a
  transaction with the change.
- **`pgaudit` in place of this.** It records statements and the database role, not the person
  or the agent. It is the right record of who reached the database outside the application, and
  a deployment runs it beside this.
- **An event with the row's values.** That is a copy of the data in a table with weaker
  controls. A project that needs earlier values keeps them in its own tables.

## Consequences

- A project gets row events for every table it lists in `TENANT_TABLES`, and request events for
  every route under the identity dependency, with no code of its own.
- Every write costs one insert for each tenant the statement wrote. Every connection the pool
  lends costs one statement. Every request a principal makes costs one insert.
- A key value that holds `:` makes a composite key read in more than one way.
- Every entrypoint that writes outside a request must name an actor, or its first write fails.
- A release that runs `make migrate` before it replaces the previous release's instances
  refuses their writes in between: the previous release sets no actor.
- A list read names its route and no row. The rows it returned are not recorded.
- A row event never has the `read` action or the `absent` outcome. An event a store recorded
  before 0.42.0 can have either. A `404` request event names a row that was not found.
- The application can insert an event that did not happen. It cannot choose when the event
  happened, or where it sorts. A deployment that needs evidence against the application itself
  ships the table to storage the application cannot write, and runs `pgaudit` beside it.
- A failed sign-in is the identity provider's record. A credential the seam rejects is recorded
  only on its `request completed` line, which is lossy and does not say why.
- Reading the audit events records no row event, because no row was written. A route that serves
  them records a request event, as any route does.
- An audit event outlives the row and the tenant it records. Retention, export and review are
  the deployment's. Where a law requires a tenant's events removed, an operator removes them as
  the owner role, never as the application role:
  [deployment.md](../../deployment.md#removing-a-tenants-audit-events).
- `user` names no concept here: an actor may be an app. The request line names the actor the
  same way, as `actor.issuer` and `actor.subject`
  ([0011](0011-every-log-line-is-declared-and-written-as-json-to-stdout.md)).
