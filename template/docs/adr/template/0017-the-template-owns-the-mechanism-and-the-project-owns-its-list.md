# The template owns the mechanism, and the project owns its list

Where a file held both a mechanism and a project's list, it is now two modules. The template
owns the mechanism and keeps updating it. The project owns a module holding only its list,
which the template writes once and an update never touches.

| The project's list | The template's mechanism |
|---|---|
| `app/errors.py` — its refusals | `app/refusal.py` — `ApiError`, `responses(...)` |
| `app/environment.py` — what its production refuses | `app/deployment.py` — the variables, `refuse_in_production` |
| `tests/tiers.py` — its tiers | `tests/tier.py` — what a tier is |
| `tests/models/layers.py` — its model layering | `tests/models/test_layering.py` |

`copier.yml` lists the project's modules under `_skip_if_exists`. The project's tests of what its
production refuses, `tests/environment/test_environment.py`, are excluded on update instead:
nothing imports a test, so a project may delete it, and `_skip_if_exists` would bring it back.

## Why

Every project adds to these lists, and the template keeps changing the code that reads them.
In one file, every template change to the code was a conflict with the project's list. The
sibling project this template was extracted alongside met one in each of these files on every
update, though the line it had changed and the line the template had changed were never the
same line.

## Considered options

- **Keep one file each, and resolve the conflict every time.** That is the cost this removes.
- **Exclude the project's modules on update, the way `example_resource` excludes the example.**
  Then a project generated before a split never receives the new module, and the template's
  code that imports it fails. `_skip_if_exists` adds a missing one and leaves a present one
  alone.
- **`_skip_if_exists` for any file a project may edit.** It re-adds a file the project deleted.
  It is safe here only because the template's own code imports each of these modules, so
  none can be deleted.

## Consequences

- A project generated before this split keeps its old copy of each list module, mechanism and
  all, because an update never touches it. The template's tests name what to delete:
  `test_every_refusal_in_app_errors_is_built_on_app_refusal` and
  `test_every_tier_is_the_template_s_tier` and
  `test_app_environment_defines_none_of_the_mechanism_itself`.
- Code the template owns imports variable names from `app/deployment.py`, never from
  `app/environment.py`, so a template update that adds a variable needs nothing from the
  project.
- `app/errors.py` re-exports `ApiError` and `responses`, and `app/environment.py` re-exports the
  mechanism, so a project's own code keeps importing from the module it always did. The
  template's own code imports from the mechanism module.
