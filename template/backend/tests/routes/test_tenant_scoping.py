"""The tenant scope is not something a route can forget.

`Database.store(tenant_id)` is the only way to obtain a `TaskStore`. `deps.get_store` is the
only caller of it for a request, so a route receives a store already scoped to its request's
tenant, and has no unscoped one to reach for instead. For a job, the only caller is a handler
that `kinds` in `app/jobs.py` builds, and it passes the tenant that the handler gets. A type
checker cannot see any of that: a route that resolved the database itself and asked for another
tenant's store would compile, serve, and leak. Row-level security is the backstop and this is
the door.
"""

import ast
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

APP = Path(__file__).resolve().parents[2] / "app"
DEPS_MODULE = "deps.py"
JOBS_MODULE = "jobs.py"
SCOPING_FUNCTIONS = frozenset({(DEPS_MODULE, "get_store"), (JOBS_MODULE, "kinds")})
"""Where a store is obtained: for a request, from its principal, and for a job, from the tenant
its handler gets."""
SUBSTRATE_FUNCTION = "database_of"


class Site(NamedTuple):
    """Where in the service an expression sits."""

    module: str
    function: str
    line: int


def _holder_of_each_node(tree: ast.Module) -> dict[ast.AST, str]:
    """The name of the function enclosing each node, for nodes inside one."""
    holder: dict[ast.AST, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                holder.setdefault(child, node.name)
    return holder


def _sites_where(matches: Callable[[ast.expr], bool]) -> list[Site]:
    """Every expression the predicate accepts, as (module, enclosing function, line)."""
    found: list[Site] = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        holder = _holder_of_each_node(tree)
        found.extend(
            Site(str(path.relative_to(APP)), holder.get(node, "<module level>"), node.lineno)
            for node in ast.walk(tree)
            if isinstance(node, ast.expr) and matches(node)
        )
    return found


def _obtains_a_store(node: ast.expr) -> bool:
    """Whether this expression calls `Database.store(...)`."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "store"
    )


def test_a_store_is_obtained_in_one_place_and_that_place_resolves_the_tenant() -> None:
    """A second caller of `Database.store()` is a second chance to pass the wrong tenant.

    The failure this prevents is a route doing its own lookup -- `database_of(request)` is
    importable and `store()` takes any string -- which reads as ordinary code and answers with
    another tenant's rows.
    """
    calls = _sites_where(_obtains_a_store)
    assert calls, "no call to Database.store() found: this test has stopped checking anything"
    stray = [c for c in calls if (c.module, c.function) not in SCOPING_FUNCTIONS]
    assert not stray, (
        f"a store is obtained outside {sorted(SCOPING_FUNCTIONS)}: {stray}. Take it from the "
        "request through StoreDep, or for a job in kinds() in app/jobs.py, from the job's tenant."
    )


def test_a_job_obtains_a_store_only_for_the_tenant_of_the_job() -> None:
    """`kinds` holds the whole `Database`, so a handler there could pass any string to `store()`.

    Postgres refuses a transaction of a job for another tenant, and the memory substrate does
    not. Each call passes the first parameter of the function that encloses it: in a handler, the
    job's tenant. The test reads each call, not each caller: a helper that takes the tenant moves
    the rule to the handlers that call it.
    """
    tree = ast.parse((APP / JOBS_MODULE).read_text(encoding="utf-8"))
    handler_of: dict[ast.AST, ast.Lambda | ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                handler_of[child] = node
    calls = [
        node for node in ast.walk(tree) if isinstance(node, ast.expr) and _obtains_a_store(node)
    ]
    stray = [
        call.lineno
        for call in calls
        if not (
            isinstance(call, ast.Call)
            and call.args
            and isinstance(call.args[0], ast.Name)
            and call in handler_of
            and call.args[0].id == _first_parameter(handler_of[call])
        )
    ]
    assert not stray, (
        f"{JOBS_MODULE} obtains a store on lines {stray} for something other than the tenant its "
        "handler gets. Take the tenant from the handler's first parameter."
    )


def _first_parameter(function: ast.Lambda | ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    parameters = [*function.args.posonlyargs, *function.args.args]
    return parameters[0].arg if parameters else None


def _is_the_app_state(node: ast.expr) -> bool:
    """Whether this expression is `app.state`, however the app was reached.

    The receiver is what makes the match mean something. Any other object may carry a
    `database` attribute -- a settings object, a cache, a test double -- and none of them is
    the substrate a request is served from.
    """
    if not isinstance(node, ast.Attribute) or node.attr != "state":
        return False
    owner = node.value
    if isinstance(owner, ast.Name):
        return owner.id == "app"
    return isinstance(owner, ast.Attribute) and owner.attr == "app"


def _reads_the_substrate(node: ast.expr) -> bool:
    """Whether this expression takes the `Database` off the app, in either spelling."""
    if isinstance(node, ast.Call):
        return (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) > 1
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "database"
            and _is_the_app_state(node.args[0])
        )
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "database"
        and isinstance(node.ctx, ast.Load)
        and _is_the_app_state(node.value)
    )


def test_the_substrate_is_taken_off_the_app_in_one_place() -> None:
    """A second reader of the app's `Database` is a second unscoped one within reach.

    The scoping test above holds the last step of the chain, and holds it well. This holds the
    step before it, which is where a duplicate hides: a module-level helper that returns the
    substrate reads as a convenience, has no tenant in its signature, and needs no caller to
    be wrong. One existed here with no callers at all, and nothing failed while it did.
    """
    reads = _sites_where(_reads_the_substrate)
    assert reads, "no read of the app's Database found: this test has stopped checking anything"
    stray = [r for r in reads if (r.module, r.function) != (DEPS_MODULE, SUBSTRATE_FUNCTION)]
    assert not stray, (
        f"the substrate is read outside {DEPS_MODULE}:{SUBSTRATE_FUNCTION}(): {stray}. "
        "Take the store from the request through StoreDep, which resolves the tenant for you."
    )
