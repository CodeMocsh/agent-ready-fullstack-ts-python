# 0010. The environment is read in one place, and run in another

Date: 2026-10-05

## Status

Accepted.

## Context

Every project adds variables, adds checks on them, and adds things its process runs. When one
file reads the environment, checks it and runs what it builds, the three interleave. Then the
list of variables is not reviewable in one place. Every project edit to that file also
conflicts with the template's edits on `copier update`.

The in-memory substrate is the correct default for a fresh clone. It is the wrong default for a
deployment, because it keeps every row in the process and loses them when the process exits. A
deployment that forgets `DATABASE_URL` starts, serves, and loses its data on the next restart.
Without a variable that says which case applies, the process cannot tell the two apart.

## Decision

Each question has one module that answers it.

- *Is this configuration legitimate?* `app/deployment.py` names the variables that
  `app/wiring.py` reads, and holds `in_development` and `refuse_in_production`.
  `app/environment.py` holds `refuse_development_settings`: the project's list of what its
  production refuses. [0015](0015-the-template-owns-the-mechanism-and-the-project-owns-its-list.md)
  says why these are two modules.
- *What did this deployment configure?* `app/wiring.py` reads the environment and builds from
  it: `build` for the substrate, `build_bundle`, `build_telemetry`, `build_timeouts` and
  `build_service_version`.
- *What is this process running?* `app/lifespan.py` holds what `app/wiring.py` builds, for the
  life of the process. It reads no variable.
  `tests/lifespan/test_lifespan.py::test_the_lifespan_reads_no_variable` holds this.

A new function goes to the module that answers its question. A `build_` function that reads no
variable belongs in `app/lifespan.py`.

Two variables are named beside other code that reads them: `OWNER_URL_ENV` in `app/migrate.py`,
and `SCHEMA_ENV` in `app/store/conn.py`. `app/migrate.py` is the release step, and it reads both
without `app/wiring.py`. `app/store/conn.py` reads `SCHEMA_ENV` when it gets no schema name.

`in_development` reads `APP_ENV`:

- Unset or `production` is production, so a deployment that forgets the variable is checked.
- `development` is the development loop. `make dev`, the contract suite and the tests set it.
- Any other value raises `UnknownEnvironment`. `APP_ENV=prod` means something to the person who
  typed it, so the process refuses it and does not guess.

Production does not start without `DATABASE_URL`. `lifespan` calls `refuse_development_settings`
before it builds anything. `refuse_in_production` raises `DevelopmentSettingInProduction` and
names every finding at once.
`tests/environment/test_environment.py::test_production_refuses_the_in_memory_substrate` holds
this.

## Considered options

- **Unset is development.** Every deployment that forgets the variable then passes, and that is
  the case the refusal exists for.
- **Refuse an unauthenticated identity seam in production too.**
  [0004](0004-a-route-cannot-escape-the-identity-seam.md) rejects this: a single-tenant tool
  behind an authenticating proxy is a legitimate deployment. The boot log still says what the
  seam does.
- **One `wiring.py` for the names, the checks and the lifespan.** Every project edit to any of
  them conflicts on `copier update`.

## Consequences

- A deployment sets `DATABASE_URL`, or sets `APP_ENV=development` on purpose. The refusal names
  the variable and both choices.
- A new convenience that only the development loop may use adds a finding to
  `refuse_development_settings`. All findings are reported at once, so one deploy fixes all of
  them.
- Code in `app/lifespan.py` that needs a variable cannot read it there. `app/wiring.py` reads it
  and passes the value.
