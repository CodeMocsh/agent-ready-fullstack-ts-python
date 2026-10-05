# 0015. The template owns the mechanism, and the project owns its list

Date: 2026-10-05

## Status

Accepted.

## Context

Some code reads a list that every project adds to: its refusals, what its production refuses,
its tiers, its model layering, the fields on its request line. The template keeps changing the
code that reads each list.

`copier update` merges a template change into a file the project changed. When the project's
lines and the template's lines sit close together, the merge conflicts. It conflicts even when
no line changed on both sides. With the list and the code that reads it in one file, every
template change to the code is a conflict on every update.

## Decision

Each list is a module of its own, and the code that reads it is another module. The template owns
the mechanism module and updates it. The project owns the list module. The template writes the
list module once, and an update never touches it.

| The project's list | The template's mechanism |
|---|---|
| `app/errors.py`: its refusals | `app/refusal.py`: `ApiError`, `responses` |
| `app/environment.py`: what its production refuses | `app/deployment.py`: the variables, `refuse_in_production` |
| `app/request_line.py`: its fields on `request completed`, beyond `tenant_id` | `app/log.py`: `name_on_request_line`, `request_completed` |
| `tests/tiers.py`: its tiers | `tests/tier.py`: what a tier is |
| `tests/models/layers.py`: its model layering | `tests/models/test_layering.py` |

- **`_skip_if_exists` in `copier.yml` carries every list module.** An update adds a list module
  that is missing and leaves a present one alone.
- **The template's code or tests import every list module**, so a project cannot delete one.
  `_skip_if_exists` therefore never re-adds a module the project removed.
- **The project's tests of what its production refuses are excluded on update instead.**
  `tests/environment/test_environment.py` is in `_exclude` in `copier.yml` for an update. Nothing
  imports a test, so a project may delete it, and `_skip_if_exists` would bring it back.
- **A list module re-exports its mechanism.** `app/errors.py` re-exports `ApiError` and
  `responses`. `app/environment.py` re-exports the mechanism from `app/deployment.py`. A project's
  own code imports from the list module.
- **The template's code imports a mechanism from the mechanism module.** It imports a variable
  name from `app/deployment.py`, never from `app/environment.py`. A template update that adds a
  variable then needs nothing from the project. The example resource imports as a project's route
  does, from `app/errors.py`.
- **A list module that defines its own copy of the mechanism fails a test.** That copy stops
  receiving the template's changes. These tests fail on one and name what to delete:
  `test_every_refusal_in_app_errors_is_built_on_app_refusal`,
  `test_app_environment_defines_none_of_the_mechanism_itself` and
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
- A list module holds only data and the project's own functions. Mechanism code put in one does
  not receive template updates, and the tests above refuse it where they can.
