# 0002. Code-first contract with committed artifacts

Date: 2026-10-05

## Status

Accepted.

## Context

A generated project is one system in two halves. The halves must agree on the shape of every
request and every response. Something must hold that agreement.

The backend declarations are the natural place to write it. FastAPI route declarations and
pydantic models execute. pytest exercises the same declarations that the exporter reads, so the
spec cannot describe behaviour the service does not have. The output of `app.openapi()` is the
same, byte for byte, on each run with pinned versions.

The usual way to use those declarations is to generate the spec and the TypeScript types at build
time and commit neither. That breaks the property this template is built on. To generate the
types, the frontend half must run the exporter of the backend half, which needs Python and a
synced virtual environment. The frontend half then cannot install, test or build alone, and mock
mode stops being a fact about the repo.

A declaration that does not match the service shows up as a type error in the other half. A `404`
declared without a model tells the spec that the response has no body. `HTTPException` returns
`{"detail": ...}`. The typed mock handlers then do not compile, before any test runs.

## Decision

Paths under `backend/` and `frontend/` name files in a generated project.

- The pydantic models in `backend/app/models/` and the route declarations in
  `backend/app/routes/` are the one place the contract is written.
- Both contract artifacts are committed: `openapi.json`, and `frontend/src/api/schema.ts` derived
  from it. `frontend/src/api/types.ts` and the mock handlers, through `frontend/src/mocks/http.ts`,
  are typed against `schema.ts`.
- `make openapi` writes both. `backend/devtools/export_openapi.py` exports the spec in process
  through `app.openapi()`, with no server. The `openapi:types` script in `frontend/package.json`
  runs a pinned `openapi-typescript` over it.
- `make openapi-check` regenerates both artifacts and fails on any diff. Its message names
  `make openapi`. `make gate` includes it, and the generated `.github/workflows/ci.yml` runs
  `make gate` in one job with both toolchains.
- `devtools/check_template.sh` runs `make openapi` on every generated project and fails if either
  committed artifact changes.
- Nobody edits a contract artifact by hand, and that includes a merge conflict. The rule is in
  `template/AGENTS.md.jinja`: take one side, then run `make openapi`.
- `copier.yml` excludes both artifacts from `copier update`. After an update the project
  regenerates them from its own code.
- Every status code a route can return declares a model. The reasons, and the settings in
  `app/main.py` that keep the spec true, are in
  `template/docs/adr/template/0008-the-spec-describes-what-the-service-actually-does.md`.

## Considered options

**Spec first.** A hand-written OpenAPI document is the source of truth, and each half is written
to match it. It is easy to review and belongs to neither half. It also does not execute. It can
say a route returns a `Task` while the service returns a `detail` string, and nothing runs to
contradict it. To keep it true takes discipline. Agents write most of the code here, and a
plausible document that is wrong is their default failure. Rejected.

**Code first, derived at build time and not committed.** This is the usual answer. It ties the
frontend half to the Python toolchain, as the Context says. Rejected.

## Consequences

- Each half stays independently operable. A contributor with only Node regenerates the types
  from the committed `openapi.json`, runs the frontend tests and builds. A contributor with only
  uv runs the backend and its tests. Mock mode works with no backend, in the repo as well as at
  run time.
- `make openapi-check` needs both halves. On a machine with only one, `devtools/gate.sh` runs that
  half and reports the check as skipped. CI installs both halves, so the check runs there.
- The contract is reviewable. An API change arrives in a pull request as a diff to
  `openapi.json`. A field that becomes optional, a status code that goes away, or a changed
  response shape is visible there. An artifact made only at build time hides the same change
  until something downstream breaks.
- The mock handlers are a checked implementation, not a fixture. openapi-msw types them against
  the committed schema. A handler that returns an undeclared status code or the wrong shape is a
  compile error, so the fake service and the real one cannot drift apart without a failure.
- Every change to a model or a route needs `make openapi` in the same commit. The gate enforces
  it, but only as a failure after the edit. Nothing stops the edit itself.
- A three-way merge of a contract artifact gives a file that neither the exporter nor the backend
  writes, and it can still type-check. The default action, for a person and for an agent, is to
  resolve the conflict by hand. Only the written rule stops that.
- The artifacts change on dependency upgrades. `app.openapi()` output is stable for pinned
  versions, but not across FastAPI or pydantic minor releases. `openapi-typescript` also changes
  its output between releases. So `make upgrade` ends with `make openapi`, and an upgrade pull
  request carries artifact changes that nobody wrote.
- FastAPI adds a `422` response to every route with a parameter or a body, and the
  `HTTPValidationError` and `ValidationError` schemas. They look like clutter and describe real
  behaviour. A check cannot tell a correct removal from a wrong one, so only the documentation
  protects them.
