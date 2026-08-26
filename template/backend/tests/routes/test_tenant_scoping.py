"""The tenant scope is not something a route can forget.

`Database.store(tenant_id)` is the only way to obtain a `TaskStore`, and `deps.get_store` is
the only caller of it, so a route receives a store already scoped to its request's tenant and
has no unscoped one to reach for instead. A type checker cannot see either half of that: a
route that resolved the database itself and asked for another tenant's store would compile,
serve, and leak. Row-level security is the backstop and this is the door.
"""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
SCOPING_MODULE = "deps.py"
SCOPING_FUNCTION = "get_store"


def _holder_of_each_node(tree: ast.Module) -> dict[ast.AST, str]:
    """The name of the function enclosing each node, for nodes inside one."""
    holder: dict[ast.AST, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                holder.setdefault(child, node.name)
    return holder


def _store_calls() -> list[tuple[str, str, int]]:
    """Every call to `.store(...)` in the service, as (module, enclosing function, line)."""
    found: list[tuple[str, str, int]] = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        holder = _holder_of_each_node(tree)
        found.extend(
            (path.name, holder.get(node, "<module level>"), node.lineno)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "store"
        )
    return found


def test_a_store_is_obtained_in_one_place_and_that_place_resolves_the_tenant() -> None:
    """A second caller of `Database.store()` is a second chance to pass the wrong tenant.

    The failure this prevents is a route doing its own lookup -- `database_of(request)` is
    importable and `store()` takes any string -- which reads as ordinary code and answers with
    another tenant's rows.
    """
    calls = _store_calls()
    assert calls, "no call to Database.store() found: this test has stopped checking anything"
    stray = [call for call in calls if call[:2] != (SCOPING_MODULE, SCOPING_FUNCTION)]
    assert not stray, (
        f"a store is obtained outside {SCOPING_MODULE}:{SCOPING_FUNCTION}(): {stray}. "
        "Take it from the request through StoreDep, which resolves the tenant for you."
    )
