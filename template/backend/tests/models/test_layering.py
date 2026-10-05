"""Nothing in `app/models/` imports upward, and every shape in it is reachable as
`from app.models import X`. `docs/adr/template/0013`."""

import ast
from pathlib import Path

import pytest

from app import models
from tests.models.layers import LAYERS

PACKAGE = Path(models.__file__).parent

THE_PACKAGE = "__init__"
"""What `from app.models import X` reaches from inside the package. Never below anything: the
package imports every module, so a module importing it is a cycle."""


def _modules() -> list[str]:
    return sorted(path.stem for path in PACKAGE.glob("*.py") if path.stem != THE_PACKAGE)


def _imports_of(module: str) -> set[str]:
    tree = ast.parse((PACKAGE / f"{module}.py").read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        if node.module == "app.models":
            found.add(THE_PACKAGE)
        elif node.module.startswith("app.models."):
            found.add(node.module.removeprefix("app.models."))
    return found


def test_the_layers_named_here_are_the_modules_that_exist() -> None:
    """A module added without a line above is a module under no rule at all."""
    assert _modules() == sorted(LAYERS)


@pytest.mark.parametrize("module", sorted(LAYERS))
def test_a_module_imports_only_from_below_it(module: str) -> None:
    crossed = sorted(_imports_of(module) - LAYERS[module])

    assert crossed == [], (
        f"app/models/{module}.py imports {crossed}, which is not below it. Either move the "
        f"shape it needs down a layer, or widen LAYERS and say why the layering changed."
    )


def test_the_layering_is_acyclic() -> None:
    """`LAYERS` describes no cycle, however long."""
    for module in LAYERS:
        reached: set[str] = set()
        pending = [module]
        while pending:
            for below in LAYERS[pending.pop()] - reached:
                reached.add(below)
                pending.append(below)
        assert module not in reached, (
            f"{module} reaches itself through {sorted(reached)}. One of those lines has to move "
            f"down, or the layering it describes is not a layering."
        )


def _defined_in(module: str) -> list[str]:
    names: list[str] = []
    for node in ast.parse((PACKAGE / f"{module}.py").read_text()).body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef):
            names.append(node.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            names.append(node.targets[0].id)
    return [name for name in names if not name.startswith("_")]


def test_every_shape_is_reachable_by_one_name() -> None:
    """Every public name a module defines is re-exported by the package."""
    missing = [
        f"{module}.{name}"
        for module in _modules()
        for name in _defined_in(module)
        if not hasattr(models, name)
    ]

    assert missing == [], f"defined but not re-exported from app.models: {missing}"
