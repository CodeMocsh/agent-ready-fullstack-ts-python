# 0015. The template owns the mechanism, and the project owns its list

Date: 2026-10-05

## Status

Accepted.

## Context

Some code reads a list that every project adds to. The table below names each list. The template
keeps changing the code around each list.

`copier update` merges a template change into a file the project changed. When the project's
lines and the template's lines sit close together, the merge conflicts. It conflicts even when
no line changed on both sides. With the list and the code that reads it in one file, every
template change to the code is a conflict on every update.

## Decision

Each list is a module of its own, and the code that reads it is another module. The template owns
the mechanism module and updates it. The project owns the list module. The template writes the
list module once, and an update never touches it. `app/identity/__init__.py` is the one exception,
and the bullet on `identity_stub` below says why.

| The project's list | The template's mechanism |
|---|---|
| `app/errors.py`: its refusals | `app/refusal.py`: `ApiError`, `responses` |
| `app/environment.py`: what its production refuses | `app/deployment.py`: the variables, `refuse_in_production` |
| `app/request_line.py`: its fields on `request completed`, beyond the tenant and the actor | `app/log.py`: `name_on_request_line`, `request_completed` |
| `tests/tiers.py`: its tiers | `tests/tier.py`: what a tier is |
| `tests/models/layers.py`: its model layering | `tests/models/test_layering.py` |
| `app/log_lines.py`: its own log lines | `app/log.py`: the format, the request line, the template's lines |
| `frontend/e2e/signed-in.ts`: how its live specs sign in | the live specs that use its `test` |
| `frontend/tests/read-budgets.ts`: its screens' read budgets, and its streams | `frontend/tests/first-draw.tsx`: `ReadBudget`, `readsOf`, `screens` |
| `frontend/tests/signed-in.ts`: how the read-budget test signs in | `frontend/tests/first-draw.test.tsx` |
| `frontend/src/app-providers.tsx`: the providers around the router | `frontend/src/main.tsx`, `frontend/tests/first-draw.tsx`, `frontend/tests/render.tsx` |
| `app/identity/__init__.py`: its resolver, `principal_for` | `app/identity/shared.py`: `Principal`, `Actor`, `Unauthenticated`, the sentinel, the probe |

- **`_skip_if_exists` in `copier.yml` carries every list module but `app/identity/__init__.py`.**
  An update adds a list module that is missing and leaves a present one alone.
- **The template's code or tests import every list module but `app/log_lines.py`**, so a project
  cannot delete one. `_skip_if_exists` therefore never re-adds a module the project removed.
  `app/log_lines.py` is the exception: no template code imports it, and ruff allows `structlog`
  there as in `app/log.py`.
- **`identity_stub` carries `app/identity/__init__.py`, not `_skip_if_exists`.** The resolver it
  ships is the identity stub, and the stub's own tests exercise it. The answer keeps the two in
  step: true brings both, and false brings neither. `app/identity/shared.py` reaches every project.
- **`frontend/e2e/signed-in.ts` does nothing under the identity stub.** A project that
  authenticates signs in there, and every live spec opens the app through it.
- **The project's tests of what its production refuses are excluded on update instead.**
  `tests/environment/test_environment.py` and `tests/started/test_environment.py` are in
  `_exclude` in `copier.yml` for an update. Nothing imports a test, so a project may delete
  either, and `_skip_if_exists` would bring it back.
- **A list module that has a mechanism module re-exports what it needs from it.** `app/errors.py`
  re-exports `ApiError` and `responses`. `app/environment.py` re-exports the mechanism from
  `app/deployment.py`. A project's own code imports from the list module.
- **The template's code imports a mechanism from the mechanism module.** It imports a variable
  name from `app/deployment.py`, never from `app/environment.py`. A template update that adds a
  variable then needs nothing from the project. The example resource imports as a project's route
  does, from `app/errors.py`.
- **A list module that defines its own copy of the mechanism fails a test.** That copy stops
  receiving the template's changes. These tests fail on one and name what to delete:
  `test_every_refusal_in_app_errors_is_built_on_app_refusal`,
  `test_app_environment_defines_none_of_the_mechanism_itself`,
  `test_app_identity_defines_none_of_the_mechanism_itself` and
  `test_every_tier_is_the_template_s_tier`.

`docs/adr/template/0011` says why the request line carries the project's fields.

## Considered options

- **One file for each list and its mechanism, and resolve the conflict on every update.** That is
  the cost this decision removes.
- **Exclude each list module on update, as `example_resource` excludes the example.** Then a
  project that has no list module never receives one. The template's code that imports it fails.
- **`_skip_if_exists` for any file a project may edit.** It re-adds a file the project deleted. It
  is safe only for a module the template's own code imports.

## Consequences

- A new list costs two modules, an entry in `_skip_if_exists`, and the import that keeps the list
  module from being deleted.
- A template update never changes a project's list. When the template's own list changes, such as
  a new refusal the template's code raises, it goes in the mechanism module. `NoSuchAsset` in
  `app/refusal.py` is one.
- A project that deletes `app/log_lines.py` gets it back, empty, on the next update. Without it,
  no project module is there for an agent to find before it reaches for `app/log.py`.
- A list module holds only data and the project's own functions. Mechanism code put in one does
  not receive template updates, and the tests above refuse it where they can.
