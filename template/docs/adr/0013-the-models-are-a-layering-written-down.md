# The models are a layering, written down

`app/models/` is a package. `shared` is at the bottom and imports nothing; every other module
may import only the modules `LAYERS` in `tests/models/test_layering.py` names for it. Every
shape is re-exported from `app/models/__init__.py` with `X as X`, so `from app.models import X`
is the one way in.

## Why

`models.py` is what the contract is generated from, and it is the second file every project
grows past. The sibling project this template was extracted alongside made it the largest file
in its backend before splitting it. Its first attempt at the split was circular: a union of every row body has
to sit above all of them, while one body had to name a vocabulary that the union's module also
held. Python reports a cycle between two modules only for some import orders, so the build that
breaks is not the one that introduced it.

A layering stays a layering only if crossing it fails. `LAYERS` is written by hand, not derived
from the imports, so a new import that crosses a layer fails the test and widening the list is a
line a reviewer reads. It is kept to what is reached: a permission nothing uses reads as a
decision and is really an oversight.

## Considered options

- **One `models.py`.** It is what shipped, and every project rewrote it. A template file every
  project rewrites is a merge conflict on every `copier update`.
- **`__all__` in `__init__.py`.** A second list of the same names, in a different place from the
  import that already says them. `X as X` is the explicit re-export a type checker reads, and
  `combine-as-imports` in `pyproject.toml` keeps it to one statement per module.
- **Importers name the module that holds a shape.** Every move between layers would then edit
  every importer.

## Consequences

- A new module needs a line in `LAYERS`, and the test fails until it has one.
- A module never imports `app.models` itself: the package imports every module, so that is a
  cycle, and the test names it as one.
- A shape left out of the re-export fails `test_every_shape_is_reachable_by_one_name`.
- Today no module imports another, so every entry in `LAYERS` is empty and the test holds
  trivially. It starts to bite at the first import between two modules, which is when a
  layering can first go wrong.
