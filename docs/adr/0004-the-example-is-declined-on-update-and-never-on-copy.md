# 0004. The example is declined on update, and never on copy

## Status

Accepted, 2026-10-04.

## Context

The template ships an example resource, `tasks`, and the rest of the template is
built on it: the store protocol, the schema and its row-level policy, the store contract, the
frontend, and `openapi.json`. A real project replaces it. The first project generated from this
template has its own `tasks`, a different model at the same paths, and every `copier update`
re-delivered the template's example there as a conflict — the largest one in its first update.

Three ways out were tried against that project's actual update, not reasoned about:

- **`_skip_if_exists` on the example's files.** It removed both conflicts, and re-added the
  example's frontend files the project had deleted. Copier renders a skip-listed file whenever
  it is missing, so a project that deleted the example gets it back on every update.
- **A question whose `false` excludes the example's files, and a validator refusing `false`
  on a fresh copy.** Copier does not expose the operation to a validator. The fresh copy went
  through and rendered a project whose `models/__init__.py` imports a file that is not there.
- **The same question, with the operation tested in `_exclude`**, which is where copier does
  expose it. This is the one taken.

Making the example fully optional — every mixed file split into framework and example, so a
copy without it still runs — was rejected for its size. It would turn the store protocol, the
schema and the frontend into conditional shapes for a choice only an existing project makes.

## Decision

`example_resource`, default true. When false, `_exclude` drops the example resource's own files
— `copier.yml` lists them — **only when `_copier_operation` is `update`**. A copy always
ships them. The answer is recorded, so a project says it once.

## Consequences

- A project that answers false keeps whatever it has at those paths, and an update never
  re-adds one it deleted. The mixed files — the store, the schema, the mocks — keep updating,
  and are where it still meets the example.
- The answer has no effect on a copy, which is what makes it safe to ask there. The help text
  says so.
- `check_template.sh` renders a copy with the answer false and refuses it if any example file
  is missing or the answer was not recorded. The update half has no check here: proving it needs
  a tagged template and a project to update, which is the dry run against the first generated
  project, not the gate.

## Amended 2026-10-04: the identity stub is declined the same way

`identity_stub`, default true, does for `app/identity.py` and its tests what
`example_resource` does for the example resource: false stops an update touching them, and a
copy always ships them, because the routes and the boot log are built on the seam. The first
generated project replaced the stub with real authentication, and its tests for the stub
conflicted with the template's on every update.
