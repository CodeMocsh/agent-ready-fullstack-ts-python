# Backend

Everything `uv` touches. [AGENTS.md](../AGENTS.md) holds the rules nothing checks; this is the
detail behind them and behind the rules the gates enforce.

## Layout

```
backend/
  app/main.py         builds the FastAPI app
  app/models/         the pydantic models the contract is made of, layered
  app/routes/         the endpoints, a module per what a caller presents
  app/refusal.py      how a route says no: ApiError and responses(...)
  app/request_line.py the fields `request completed` names, beyond tenant_id -- yours
  app/connection_budget.py  what one process holds open to Postgres beside the pool -- yours
  app/admission.py    how many requests of one actor a process admits, and the 429
  app/admission_limits.py  the numbers admission holds each actor to -- yours
  app/errors.py       every refusal a route can answer, once each -- yours
  app/deps.py         what a route is handed
  app/wiring.py       what this deployment configured, read from the environment
  app/deployment.py   the variables' names, and how production refuses
  app/environment.py  what production refuses to carry -- yours
  app/lifespan.py     what this process runs; reads no variable
  app/serve.py        the one-origin entrypoint: app.main plus the frontend bundle
  app/identity/       who a request is: a tenant and an actor
    __init__.py       the resolver, principal_for -- ships as a stub; yours once you replace it
    shared.py         what every resolver shares: Principal, Actor, Unauthenticated -- the template's
  app/audit_events.py who read or changed which rows, and who acts: serve, acting_as
  app/request_events.py one request event for each request a principal makes
  app/log.py          how the application logs, and the template's own lines
  app/log_lines.py    every other line the application logs -- yours
  app/telemetry.py    traces and metrics over OTLP; nothing else instruments the app
  app/loop_watch.py   reports a held event loop, in every deployment
  app/waits.py        bounds each wait outside the process; beside runs short work nobody awaits
  app/pacing.py       every wait on time, named for why it waits
  app/loops.py        rounds: each loop that runs until its process stops, through a database outage
  app/jobs.py         each kind of job, and its handler -- yours
  app/worker.py       the worker process, `python -m app.worker`: claims and runs the jobs
  app/migrate.py      the entrypoint `make migrate` runs
  app/store/          the data layer; ddl.py is the schema, as data
  tests/              mirrors app/, except tests/started/ and each tier
  tests/routed.py     the app a route test drives: its routers, and no lifespan
  tests/started/      the tests that start the app
  tests/tiers.py      what a tier needs and what runs it
  tests/test_gate.py  tests the repository rather than the app
```

## The contract

- `openapi.json`, `frontend/src/api/schema.ts` and `frontend/src/api/parts.ts` are generated
  from `backend/app/`. Change the backend, run `make openapi`, and commit all four. `make pre-commit` fails on an artifact that
  is out of date.
- Declare every status code a route can return, with a `model` for anything that has a body. An
  undeclared code is invisible to the other half. A refusal is a class in `app/errors.py`: raise
  it, and name it in `responses(...)` on the route.
- Every `frontend/tests/api/*contract.test.ts` runs against the mock handlers and against the
  real backend. Seed data differs between the two by design: memory hands out `"1"`, Postgres
  hands out uuids. Seed rows are asserted in `tests/routes/test_tasks.py` and nowhere else.

## Conventions

- **Log through a function in `app/log_lines.py`.** ruff refuses `logging` and `structlog`
  outside it, `app/log.py` and `tests/log/`, and `print` outside `devtools/`. A field is a
  parameter somebody declared. `tests/log/test_log.py` holds a canary to that.
- **A span attribute leaves the process only if `SPAN_ATTRIBUTES` in `app/telemetry.py` names
  it**, and a metric attribute only if `METRIC_ATTRIBUTES` does. Never a path, a query string, a
  header, an address, query text, a tenant or a user. `tests/telemetry/` holds a canary to that.
- **`app/wiring.py` reads the environment and builds from it; `app/deployment.py` names the
  variables and refuses, in production, what `app/environment.py` says only the development
  loop may carry; `app/lifespan.py` reads nothing.** A `build_` function that reads no variable belongs
  in `lifespan.py`. Everything below them takes its DSN, schema and migrate mode as arguments.
- **A step that holds the event loop is reported.** `make dev` runs asyncio's debug mode, which
  logs a WARNING on the `asyncio` logger for each step longer than 0.1 s. The line names the
  task, not the route; the request line beside it names the route. One such line at each start
  is uvicorn importing the app. In the tests, a step that uses more than `HELD_SECONDS` of CPU
  on the reference core, in `tests/event_loop.py`, is an error at teardown of the test that held
  it. The CPU time of a garbage collection that runs during the step does not count. A fixture
  wider than one test puts the error on the next test. A test that holds the event
  loop on purpose says so with `asyncio_reports.held_on_purpose()`.
- **The limit is in seconds on the reference core, a Google Cloud E2 core.** A Mac runs the same
  Python several times faster. At the start of each test process, the tests time a fixed
  workload on the machine and scale the limit to it, so a fast machine allows less CPU. A pass
  on a fast machine is then a pass on the reference core. The error names the CPU a step used
  on this machine and what it comes to on the reference core.
- **A thread frees the event loop only for code that releases the interpreter lock.** The tests
  and `app/loop_watch.py` measure the CPU of the loop's own thread. CPU work in
  `asyncio.to_thread` or in a `def` route runs on another thread, so neither one reports it.
  That work holds the interpreter lock, unless an extension releases it, and the loop waits for
  the lock. Pure Python releases the lock every switch interval, 5 ms by
  default. The loop then runs, but late. Parsing and validation in an extension keep the lock
  until the call returns, pydantic-core included. In one deployment, pydantic-core validated
  6,500 rows in 80 ms in `asyncio.to_thread`. A tick on the loop ran 57 to 73 ms late. In a
  deployment, only the `asyncio.event_loop.delay` histogram shows such a hold. A thread frees the
  loop for a wait, for I/O, and for an extension that releases the lock, such as `hashlib` on a
  large input. Bound other CPU work in a request by what the request answers. When it stays too
  large, do less of it, or run it in another process.
- **A call that waits without `await` uses no CPU, so the tests do not report it.** ruff's
  `ASYNC2` rules refuse a sleep, a file, a process or an HTTP request called directly in an
  `async def`. Nothing refuses a wait inside a synchronous helper, a database driver or a socket
  call. Keep each of those in a `def` route or in `asyncio.to_thread`, and a call to a system
  outside the process in `asyncio.to_thread` inside `waiting`.
- **A deployment reports a held event loop too.** While the app serves, the lifespan runs
  `app/loop_watch.py`. A tick every `TICK_SECONDS` adds how late it ran to the
  `asyncio.event_loop.delay` histogram, in seconds, when telemetry is on. When the loop's
  thread uses more than `REPORTED_CPU_SECONDS` of CPU between two ticks, the WARNING line
  `event loop held` names `cpu_ms`, `late_ms`, `code.function.name` and `code.line.number`.
  The function is the innermost frame of this project's own code where the loop was, or the
  innermost frame when none is. A wait uses no CPU and is not named. A hold in code that keeps
  the interpreter lock names no function. `docs/deployment.md` says what to alert on.
- **Each wait in a request has a bound under the request budget.** Wrap each call to a system
  outside the process in `async with waiting(seconds, on="sqlserver.login")`, from
  `app/waits.py`. The block is cancelled at `seconds`, and `WaitTimedOut` names `on`. In a
  request, a bound that is not under `REQUEST_BUDGET_SECONDS` raises `WaitOutlastsTheRequest`
  before the call starts. A call in `asyncio.to_thread` stops being awaited at the bound and runs
  on in its thread, so hand its driver the same constant as its own timeout. `waiting` is async,
  so a call to a system outside the process goes in an `async def` route, never in a `def` route.
  A WebSocket is not a request, and is not marked.
- **Wrap the call into the port, not the call inside the adapter.** A route test replaces the
  adapter with a double, so a `waiting` inside the real adapter runs in no route test. Keep the
  bound as a constant beside the port. The adapter hands it to its driver, and the caller hands
  it to `waiting`. The double then runs inside the same block, and the route test fails.
- **Make a wait past the budget a job.** Name its kind in `app/jobs.py`, with a handler that takes
  the job's tenant and the job's input, and returns the outcome. Build the handler in `kinds`, and
  take each store there, for the job's tenant. A route asks for the job with
  `Database.jobs(tenant_id).ask(kind, input)`, through `JobsDep`, and answers `202` with the
  job. The browser reads `GET /jobs/{id}` again until the job is `done` or `dead`. On Postgres the
  worker process, `python -m app.worker`, runs it as the actor that asked. A job runs at least
  once, so a handler does on a second run what it did on the first. A handler that raises is
  tried again, and after `MAX_ATTEMPTS` attempts the job is `dead`. An input and an outcome are
  JSON that `jsonb` can hold: an input that cannot raises `UnstorableJson`, which the route
  answers with `UnstorableInput`, and an outcome that cannot fails its attempt.
  [adr/template/0027](adr/template/0027-work-that-outlives-its-request-is-a-job-that-a-worker-process-claims.md) says why.
- **Start short work the caller does not wait for with `beside(work, name=..., within=...)`.**
  `within` is under `REQUEST_BUDGET_SECONDS`, and past it the work fails with `WaitTimedOut`.
  `work` is a coroutine, and the task ends with its result. The task runs outside every request,
  and acts as the actor of the code that started it. It ends with its process. A task that
  `asyncio.create_task` or `BackgroundTasks` starts is still in its request, so its `waiting`
  raises: ruff refuses both, and `asyncio.ensure_future`.
  A task that raises writes the ERROR line `work beside the requests failed`. The lifespan
  cancels each task that still runs at shutdown, and `work beside the requests cancelled` names
  it. A task that does not end within `CANCELLED_WITHIN_SECONDS` of the cancel fails the
  shutdown with `BesideOutlivedItsCancel`.
- **Share work that several callers await at once with `SharedWork`.** Hold one
  `shared: SharedWork[K, T] = SharedWork(name)` for each kind of work, and
  `await shared.joined(key, start)`. The first call for `key` starts `start()`, and each call
  that arrives before the run ends awaits the same run. A cancelled caller stops awaiting, and
  the run goes on for the others. Each run is in a request, whoever starts it, so each `waiting`
  in it has a bound under the budget. A wait that may last longer is a job.
- **The key names everything the result of shared work depends on.** Each caller gets the result of
  the work that the first caller started, in the context of the first caller: its actor, its
  request id and its trace. Put the tenant, the actor and the arguments in the key. A key without
  the tenant gives one tenant the rows of another.
- **Shared work reports how each run ended.** A failure raises in each caller that awaits the
  run, and the ERROR line `shared work failed` names it. The lifespan cancels a run still in
  flight at shutdown, and the WARNING line `shared work cancelled` names it.
- **Each task has one of five homes.** Work that waits past the budget is a job. Short work the
  caller does not wait for goes in `beside`. Work a caller awaits together goes in an
  `asyncio.TaskGroup`, and so does an `asyncio.gather` without
  `return_exceptions=True`: it leaves the others running after the first failure. Work several
  callers await at once goes in `SharedWork`. A loop that the lifespan starts and stops in its own
  order goes in one module of yours, named in the `per-file-ignores` of `pyproject.toml` as
  `["TID251"]`, with its reason. That entry also lets the module import `logging` and the other
  banned modules and a bare sleep, so keep it to the loops. A loop paces itself with
  `app.pacing.every` all the same, and a loop that waits on rows this system writes waits on a
  wake, never on a sleep.
- **A loop that runs until its process stops runs each round through `app.loops.rounds`.** A
  round the database did not serve waits the refusal's `Retry-After` and runs again, and the
  WARNING line `loop waits for the database` names the loop. A round raises `DatabaseUnavailable`
  only when the database served it nothing: a round that the database served and then stopped
  serving ends. Past `UNAVAILABLE_WITHIN_SECONDS` of rounds that raise it, the loop raises
  `DatabaseGone` and stops its process. Any other error stops it at once, so do
  not catch it in the round. `tests/loops/` refuses a `while True` with no `break` and no
  `return` outside `app/loops.py` and `app/pacing.py`.
- **Postgres is not wrapped.** The process refuses to start on a bound that is not under the
  budget. [adr/template/0021](adr/template/0021-each-wait-in-a-request-has-a-bound-under-the-request-budget.md) says why.
- **A route test starts nothing.** It mounts the routers under test with `tests.routed.routed`,
  which assembles them as `create_app` does, with no lifespan. Hand `routed` the `database`
  fixture, and `**app_state` when the lifespan puts other values on `app.state`. The template's
  lifespan puts none there. Mount a
  requirement router, such as `tenant.router`, so the requirement is part of what the test
  asserts.
- **`routed` builds the app for a set of routers once per process, and shares it.** Each test
  gets a new `app.state` and no `dependency_overrides`, and may change both. A test that changes
  what the app serves, such as a route, a middleware, an exception handler or an attribute, fails
  at its teardown. A test that asks `routed` for an app while it configures telemetry fails too,
  and so does a test that asks `routed` twice for the same routers. Build each of those apps with
  `tests.routed.assembled`, which no other test shares. `routed` shares by
  the routers it is given, so build each router once, at import.
- **`create_app` adds nothing but the lifespan.** Each requirement's router goes in `ROUTERS`, and
  everything around a route goes in `assemble`. A route test then serves what the app serves,
  and `tests/main/test_main.py` fails when it does not.
- **A test that starts the app goes in `tests/started/`**, or in a tier: the lines the lifespan
  logs, what it refuses, what it builds before the first request. Anywhere else it fails, and the
  failure names both places. A test that builds `create_app()` and never enters `with
  TestClient(...)` starts nothing, and may stay. [adr/template/0018](adr/template/0018-a-route-test-never-starts-the-app.md)
  says why.
- **A lifespan step holds the event loop like a route step**, and the tests report it the same
  way. No request waits for it, because uvicorn serves no request until the lifespan's startup
  returns. The limit is for a step that a request waits on. So startup CPU work past the limit,
  such as compiling or validating a large document, runs in
  `await asyncio.to_thread(compile_spec)`, as the lifespan does for `build_routes`. The thread frees nothing. It
  only takes the work out of what the tests measure. Do this before the first request, and
  nowhere else.
- Nothing degrades from Postgres to memory. A bad `DATABASE_URL` fails at boot, and an unset one
  is refused unless `APP_ENV=development`. The application refuses to start if it can see
  `DATABASE_OWNER_URL`.

## Tenancy and the database

- `Database.store(tenant_id)` is the only way to get a store, and the tenant is a parameter on no
  method. A store you can hold is a store already scoped.
- Every index on a tenant table leads with `tenant_id`, or the policy's predicate cannot be
  satisfied and Postgres scans.
- **A store read answers one page.** A list takes `after` and answers at most `PAGE_MAX` rows,
  at most `PAGE_BYTES` bytes, and `next`. `fetch_page` in `app/store/pages.py` reads it: its SQL
  reads `PAGE_MAX + 1` rows on an index that leads with `tenant_id` and ends with the column the
  list is ordered by, such as `tasks_seq_idx`, and stops at `PAGE_BYTES`. Give it a `SELECT` with
  no `ORDER BY` and no `LIMIT`, and the one column to order by. Nothing else in `app/` reads many
  rows, and `tests/store/test_pages.py` fails on a read that does. `pages_read` on the request
  line counts the pages each request read. A task cursor names a task by
  its id, never by `seq`. The audit events, which no route serves, page by `seq`. A task cursor
  that names no task of the tenant answers `None`, and the route answers `404`. An audit cursor
  that names no event raises `NoSuchAuditEvent`. `tests/store_contract.py` holds both substrates
  to it, and `tests/integration/test_pages_on_postgres.py` holds Postgres to the `LIMIT`. A test
  that needs the whole list reads it with `tests/pages.py`.
  [adr/template/0026](adr/template/0026-every-store-read-is-a-page.md) says why.
- **Every column of variable width has a byte bound.** `COLUMN_BYTES` in `app/store/ddl.py`
  declares it, and Postgres refuses a write over it, from any writer. Give a new column its bound
  in the same change, and bound the request model that writes it more tightly, so the API answers
  a `422` first. The bounds on one row add up to at most `PAGE_BYTES`. A file, or a copy of a
  whole document, goes in object storage, and the row holds a reference to it.
  `tests/integration/test_bounds_on_postgres.py` fails on a column with no valid bound.
  [adr/template/0031](adr/template/0031-the-database-bounds-the-bytes-of-every-value.md) says why.
- A policy compares `tenant_id` to `current_setting`, as `app/store/ddl.py` writes it. A join or a function that reads
  the row makes Postgres evaluate it for every row, and the index cannot help.
- `MemoryDatabase` stays. The gate runs on it and must not need a daemon, and one implementation
  of a `Protocol` is a shape nothing checks.
- **Migrations are additive, and the application never applies them.** No `DROP COLUMN`, no
  `ALTER COLUMN ... TYPE`, no `RENAME`. `make migrate` applies; `make schema` and `make roles`
  regenerate the committed artifacts in `deploy/`.
- **The schema is data.** `app/store/ddl.py` holds one statement per four-digit key, and
  `make migrate` runs all of them every time. A shipped entry never changes: a column added later
  lands twice, once in its table's `CREATE` and once keyed from `REPAIR_BAND` in `app/store/ddl.py`.
  `.schema-baseline.json` records each body, and `tests/store/test_schema.py` refuses an edit to one. Run
  `make schema` and commit both files. [schema.md](schema.md) says what the migration system will
  not do.
