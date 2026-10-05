# 0001. The store is two implementations behind one contract, and the in-memory one is permanent

Date: 2026-10-05

## Status

Accepted.

## Context

A deployment needs Postgres. The fast tier must not need a daemon. `make pre-commit` runs on every
commit, and people commit around a gate that needs a container. A fresh clone must run `make dev`
with no infrastructure.

One implementation behind a `Protocol` is a shape that nothing checks. The important rules are
the ones that one implementation keeps by accident:

- A missing task is reported, not raised.
- An id that cannot exist is a `404`, not a `500`.
- `list()` returns tasks in creation order.

Only a second implementation that runs the same suite makes these rules a contract.

## Decision

- `app/store/__init__.py` declares two protocols, `Database` and `TaskStore`. `MemoryDatabase` in
  `app/store/memory.py` and `PostgresDatabase` in `app/store/pg.py` implement both.
- `TaskStoreContract` in `tests/store_contract.py` holds the contract once. Each substrate runs it
  from a suite of its own: `tests/store/test_store_contract.py` for memory, and
  `tests/integration/test_store_contract.py` for Postgres. A tier that is not selected is
  reported as not run.
- The in-memory substrate is permanent. `make test` runs on it, and so does `make dev` with no
  `DATABASE_URL`. `wiring.build()` selects it when `DATABASE_URL` is unset.
  `refuse_development_settings` in `app/environment.py` refuses it in production.
- The in-memory substrate seeds three tasks, `SEED` in `app/store/memory.py`. Postgres starts
  empty. Memory ids are small integers and Postgres ids are uuids. A suite that passes against
  both cannot assume either.
- On the backend, only `tests/routes/test_tasks.py` asserts seed rows. The frontend mock spec,
  `frontend/e2e/tasks.spec.ts`, may name them, because those rows are the mock's own, in
  `src/mocks/store.ts`. `frontend/e2e/tasks.live.spec.ts` runs against either substrate, so it
  creates the row it asserts and then deletes it.
- `TaskStore.update` returns `None` and `TaskStore.remove` returns `False` for a missing task.
  This passes the *Fail loudly* test in `AGENTS.md`. The design plans for a missing task. The
  contract names it: `404` with a model, in `openapi.json`. The route reports it.
- No store has a `reset()`. A test that needs a clean substrate builds a clean app with
  `create_app()`. `database_of` in `app/deps.py` reads the substrate from the request, not from a
  module-level singleton. One test process can therefore hold two apps on two substrates.
- `app/store/__init__.py` does not re-export `app.store.pg`, and `pg.py` imports asyncpg inside
  the function that connects. A process on the in-memory substrate never loads the driver.

## Considered options

- **Postgres only.** This puts a daemon on the default path, in the gate that runs on every
  commit. It also removes the first run with no infrastructure, which is why people start a
  project from a template.
- **In-memory only, with Postgres left to the project.** Every project replaces the store in its
  first week. In that week the project invents its data layer with no migration discipline, no
  committed schema and no transaction boundary. A template must carry those, for the same reason
  it carries `openapi.json`.
- **A parametrised suite over both substrates.** It must skip the Postgres half when no server is
  present. A skipped test exits 0, the same as a test that passed.
- **A Copier question, `database: none | postgres`.** It doubles the template's variant matrix.
  Half of the generated projects would never see the pattern. Shipping both costs one runtime
  dependency, asyncpg, and `backend/pyproject.toml.jinja` states why it stays.
- **`update` and `remove` raise `TaskNotFound`.** The return value already passes all three
  conditions of *Fail loudly*. A raise also makes the store disagree with the spec that the
  frontend's types are generated from.

## Consequences

- Every rule on the store costs two implementations. A rule that the in-memory substrate cannot
  keep is a rule that the design has not specified.
- The in-memory substrate has no row-level security policy. It keeps one list per tenant, and
  `store()` raises `TenantUnset` on an empty tenant. The tenancy tests in `TaskStoreContract`
  hold both substrates to the same result. [0002](0002-tenant-isolation-is-forced-and-always-on.md)
  is the Postgres half.
- Seed rows exist on one substrate only. A test that asserts a seed row anywhere else passes on
  `make dev` and fails against a real database.
- A process on Postgres needs the release step before it serves.
  [0003](0003-the-application-never-applies-ddl.md) says why.
