# 0022. The connections of every process fit inside `max_connections`

Date: 2026-11-06

## Status

Accepted.

## Context

Postgres refuses a new connection past `max_connections`. Each process of each instance holds a
pool, so the number of connections grows with the number of instances. Before this decision,
nothing related the two numbers. Each pool held up to 10 connections, whatever the server
allowed.

In a project generated from this template, one browser tab sent about 1,200 reads a minute.
Requests took 10 s to 50 s, and the host added instances, from 1 to 6. Each process held up to
22 connections: the template's pool, a pool of its own and two `LISTEN` connections. The server
allowed about 100. Connections went from 9 to 115. A loop in the project died on
`TooManyConnectionsError` and stopped its process, and users got `500` and `503` for about
3 minutes. The last of those deaths came during a rollout, when the old revision's 6 instances
and the new revision's 6 held connections at the same time.

Two things were missing. Nothing said how many connections each process may hold. And a
connection that the server refused was a failure that nothing planned for. `AcquireTimedOut`
covered a full pool, not a server that refuses.

## Decision

- **Each process gets an equal share of what the server allows.** The share is
  `(max_connections - reserved - OUTSIDE) / DATABASE_INSTANCES`, rounded down. `budgeted` in
  `app/store/budget.py` calculates it. `tests/store/test_budget.py` holds the arithmetic.
- **The server's limits are read from the server.** Before the pool opens, `PostgresDatabase`
  reads `max_connections`, `superuser_reserved_connections` and `reserved_connections` from
  `pg_settings` on one connection, and closes it. `reserved` is the sum of the last two. The
  application role cannot use those connections.
- **The project declares what it holds beside the template's pool.** `app/connection_budget.py`
  is the project's list, as [0015](0015-the-template-owns-the-mechanism-and-the-project-owns-its-list.md)
  says. `IN_PROCESS` names each pool or connection that one process holds beside the template's
  pool. `OUTSIDE` names each holder that is not one of the processes. The template ships the
  release step in `OUTSIDE`, with one connection.
- **The template's pool takes what remains of the share.** Its size is the share less
  `IN_PROCESS`. The process refuses to start with `OverBudget` when that is less than one. The
  message names each number and what to lower.
  `test_the_pool_takes_what_remains_of_the_process_s_share_of_the_server` and
  `test_draws_the_server_cannot_hold_refuse_to_open` hold this on Postgres.
- **`DATABASE_INSTANCES` is the most processes that hold connections at once.** It counts the
  processes of a revision that drains during a rollout, and each worker process. It is not the
  platform's most instances. `wiring.build_draws` reads it. Production refuses to start without
  it, and on a value that is not a whole number of at least one. The development loop on
  Postgres is two processes, the web process and the worker, so there an unset value is two.
  `tests/wiring/test_wiring.py` holds this.
- **Each process holds `TEMPLATE_IN_PROCESS` beside the template's pool.** It is the worker's
  `LISTEN` connection, in `app/store/budget.py`. Every process takes the same share, so the web
  process keeps it and does not use it.
  [0027](0027-work-that-outlives-its-request-is-a-job-that-a-worker-process-claims.md) says why.
- **`Draws.pool` fixes the size of the template's pool.** The budget still holds it to the
  share. `wiring.build_draws` never sets it; a test sets it for a pool of one.
- **The budget is visible.** `PostgresDatabase.budget()` returns it once the pool is open, and the
  line `connections budgeted` names each number.
- **A request that the database does not serve is refused, and only that request.** The pool
  raises `AcquireTimedOut` when no connection comes within `Timeouts.acquire`, and
  `ConnectionRefused` when a new one does not open: the server refuses it, or nothing answers at
  its address. It raises `ConnectionLost` when a lent connection ends during the unit of work,
  as at a restart or a failover. asyncpg reports that end in several ways, so the pool reads
  whether the connection ended, and not the type of the error. Each is `DatabaseUnavailable` in
  `app/store/__init__.py`. `app/main.py` answers `DatabaseUnavailable` with `NoConnectionFree`
  from `app/refusal.py`: a `503`, with a `Retry-After` of `Timeouts.acquire` in whole seconds,
  and the line `database unavailable`.
  `test_a_connection_the_server_refuses_is_a_refusal_of_one_request` and
  `test_a_connection_ended_during_a_statement_is_lost_and_gives_its_slot_back` hold the mapping
  on Postgres, and `test_a_request_the_database_did_not_serve_is_refused_with_a_time_to_ask_again`
  the `503`.
- **A request that the database does not serve records no request event.** Recording it would
  wait for the database too. [0017](0017-the-database-records-every-write-and-the-request-layer-every-request.md) says so.
- **The same holds when the database does not serve the request event.** Each request a
  principal makes takes a second connection, to record its event, after its route has answered.
  `RecordsRequests` in `app/request_events.py` sends the same `503` in place of the route's
  response. `test_a_request_whose_event_got_no_connection_is_refused_with_a_time_to_ask_again`
  holds this.
- **Every route that requires a tenant declares the `503`.** The router in `app/routes/tenant/`
  declares `NoConnectionFree`, so each route under it carries the `503` in `openapi.json`.
  `test_every_route_that_requires_a_tenant_declares_what_the_template_refuses_it` holds this.

## Considered options

- **A fixed pool size, and a check that the product fits.** The check needs the same numbers,
  and a fixed size is wrong for each server it was not chosen for. A number that the budget
  gives is right for each server.
- **A reserve for every other holder, in one number.** A pool inside the process is not outside
  it. With one reserve, the template's pool alone fits its share and the process holds more.
- **Read the platform's most instances.** No platform says how many processes overlap during a
  rollout, and a revision that drains holds its connections until its last open request ends.
  The deployment declares the number.
- **Check the budget at plan time, in the infrastructure code.** The template ships no
  infrastructure code. The server's `max_connections` is known only to the server, and a
  database shared by two services changes under the plan.
- **Let a refused connection raise, and answer `500`.** A client retries a `500`. A `503` with
  `Retry-After` tells it when to ask again, and the contract names it.
- **A pooler such as PgBouncer in front of Postgres.** It moves the limit and does not remove it.
  It also changes what `SET` and a prepared statement mean, which the pool relies on. A
  deployment may add one, and then sets `max_connections` to what the pooler allows.

## Consequences

- A deployment must set `DATABASE_INSTANCES`, and nothing checks it against the platform. A
  number that is too low lets the processes take the server past `max_connections` again. A
  number that is too high gives each pool fewer connections.
- The pool can be large on a large server. Nothing in this decision limits how many requests
  one client sends; a large pool only means that the server, and not the pool, is the limit.
- Each process opens one more connection at start, and closes it before the pool opens.
- A project's own pool reads its size from `IN_PROCESS`. A pool that opens more than its entry
  breaks the budget, and nothing detects it.
- A worker that runs as its own service is in `OUTSIDE`, with the most connections all of its
  instances hold.
- A `503` from a database that is unavailable and a `503` from the platform look the same to the
  client. The `detail` and the line `database unavailable` tell them apart.
- A connection that ends during `COMMIT` leaves the outcome unknown. So a unit of work that
  raised `ConnectionLost` may have committed, and only an idempotent unit runs again.
- A request that wrote rows and then was not served keeps the row events of its writes. Only
  its request event is missing. Its client gets a `503`, and a retry of a create can create twice.
