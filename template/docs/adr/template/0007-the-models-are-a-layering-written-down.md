# 0007. The models are a layering, written down

Date: 2026-10-05

## Status

Accepted.

## Context

The models are what the contract is generated from. One `models.py` is the second file that a
project grows past, and it becomes the largest file in the backend. A project then splits it.

The first split is often circular. A union of every row body must sit above all the bodies. One
body must name a vocabulary that the module of the union also holds. Python reports a cycle
between two modules only for some import orders. The build that breaks is then not the build
that introduced the cycle.

A layering stays a layering only if an import that crosses it fails.

## Decision

- `app/models/` is a package. `shared` is at the bottom and imports nothing. Each other module
  may import only the modules that `LAYERS` names for it.
- `LAYERS` is in `tests/models/layers.py`. The project owns that module, and the template writes
  it once. `tests/models/test_layering.py` holds the tests, and the template owns it.
  [0015](0015-the-template-owns-the-mechanism-and-the-project-owns-its-list.md) records that
  split.
- `LAYERS` is written by hand and not derived from the imports. A new import that crosses a
  layer fails `test_a_module_imports_only_from_below_it`. To widen `LAYERS` is a line that a
  reviewer reads.
- Each entry in `LAYERS` lists only the imports that its module makes. A permission that nothing
  uses looks like a decision and is an oversight.
- `test_the_layers_named_here_are_the_modules_that_exist` fails on a module with no entry.
  `test_the_layering_is_acyclic` fails on a cycle of any length.
- `app/models/__init__.py` re-exports every shape as `X as X`. `from app.models import X` is the
  one way in. `test_every_shape_is_reachable_by_one_name` fails on a shape that is not
  re-exported.
- `combine-as-imports` in `backend/pyproject.toml` keeps the re-export to one statement per
  module.

## Considered options

- **One `models.py`.** Every project rewrites it. A template file that every project rewrites is
  a merge conflict on every `copier update`.
- **`__all__` in `__init__.py`.** It is a second list of the same names, in a different place
  from the import that already states them. `X as X` is the explicit re-export that a type
  checker reads.
- **Importers name the module that holds a shape.** Each move of a shape between layers then
  changes every importer.

## Consequences

- A new module needs an entry in `LAYERS`. The tests fail until it has one.
- A module in `app/models/` never imports `app.models`. The package imports every module, so
  that import is a cycle. `test_a_module_imports_only_from_below_it` fails on it and names
  `__init__`.
- As the template ships it, no module imports another, so every entry in `LAYERS` is empty and
  the tests cannot fail. They start to hold at the first import between two modules. That is the
  first time a layering can go wrong.
