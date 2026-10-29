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
  app/migrate.py      the entrypoint `make migrate` runs
  app/store/          the data layer; ddl.py is the schema, as data
  tests/              mirrors app/, except tests/started/ and each tier
  tests/routed.py     the app a route test drives: its routers, and no lifespan
  tests/started/      the tests that start the app
  tests/tiers.py      what a tier needs and what runs it
  tests/test_gate.py  tests the repository rather than the app
```

## The contract

- `openapi.json` and `frontend/src/api/schema.ts` are generated from `backend/app/`. Change the
  backend, run `make openapi`, and commit all three. `make pre-commit` fails on an artifact that
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
- **A call that waits without `await` uses no CPU, so the tests do not report it.** ruff's
  `ASYNC2` rules refuse a sleep, a file, a process or an HTTP request called directly in an
  `async def`. Nothing refuses a wait inside a synchronous helper, a database driver or a socket
  call. Keep each of those in a `def` route or in `asyncio.to_thread`.
- **A route test starts nothing.** It mounts the routers under test with `tests.routed.routed`,
  which assembles them as `create_app` does, with no lifespan. Hand `routed` the `database`
  fixture, and `**app_state` when the lifespan puts other values on `app.state`. The template's
  lifespan puts none there. Mount a
  requirement router, such as `tenant.router`, so the requirement is part of what the test
  asserts.
- **`routed` builds the app for a set of routers once per process, and shares it.** Each test
  gets a new `app.state` and no `dependency_overrides`, and may change both. A test that changes
  what the app serves, such as a route, a middleware, an exception handler or an attribute, fails
  at its teardown. A test that asks `routed` for an app while it configures telemetry fails too.
  Build its app with `tests.routed.assembled`, which no other test shares. `routed` shares by
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
  way. Startup work that is CPU work, such as compiling or validating a large document, runs in
  `await asyncio.to_thread(compile_spec)`.
- Nothing degrades from Postgres to memory. A bad `DATABASE_URL` fails at boot, and an unset one
  is refused unless `APP_ENV=development`. The application refuses to start if it can see
  `DATABASE_OWNER_URL`.

## Tenancy and the database

- `Database.store(tenant_id)` is the only way to get a store, and the tenant is a parameter on no
  method. A store you can hold is a store already scoped.
- Every index on a tenant table leads with `tenant_id`, or the policy's predicate cannot be
  satisfied and Postgres scans.
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
  `.schema-baseline.json` records each body, and `make test` refuses an edit to one. Run
  `make schema` and commit both files. [schema.md](schema.md) says what the migration system will
  not do.
