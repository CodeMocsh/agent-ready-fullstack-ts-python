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
  app/identity.py     which tenant a request is -- ships as a stub returning "default"
  app/log.py          how the application logs, and the template's own lines
  app/log_lines.py    every other line the application logs -- yours
  app/telemetry.py    traces and metrics over OTLP; nothing else instruments the app
  app/migrate.py      the entrypoint `make migrate` runs
  app/store/          the data layer; ddl.py is the schema, as data
  tests/              mirrors app/, except tests/integration/, which is a tier
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
  variables and refuses, in production, what `app/environment.py` says only development may
  carry; `app/lifespan.py` reads nothing.** A `build_` function that reads no variable belongs
  in `lifespan.py`. Everything below them takes its DSN, schema and migrate mode as arguments.
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
  lands twice, once in its table's `CREATE` and once in the `0200_` repair band.
  `.schema-baseline.json` records each body, and `make test` refuses an edit to one. Run
  `make schema` and commit both files. [schema.md](schema.md) says what the migration system will
  not do.
