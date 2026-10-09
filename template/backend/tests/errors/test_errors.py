"""A refusal a route raises is declared on it, and a refusal declared is raised somewhere.

Read from the source rather than driven, so a raise on a branch no test reaches still counts.
`docs/adr/template/0006`.
"""

import ast
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar, NamedTuple

from fastapi import HTTPException

from app import errors, refusal
from app.main import create_app
from app.refusal import ApiError, responses
from tests.routes.walk import endpoints_of

SOURCE = Path(__file__).resolve().parents[2] / "app"


def _error_classes() -> dict[str, type[ApiError]]:
    return {
        name: value
        for name, value in vars(errors).items()
        if isinstance(value, type) and issubclass(value, ApiError) and value is not ApiError
    }


ERRORS = _error_classes()


def _module_of(path: Path) -> str:
    parts = path.relative_to(SOURCE.parent).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _nodes() -> Iterator[tuple[Path, ast.AST]]:
    """Every AST node in every module under `app/`, with the file it is in."""
    for path in sorted(SOURCE.rglob("*.py")):
        if "__pycache__" not in path.parts:
            for node in ast.walk(ast.parse(path.read_text())):
                yield path, node


def _name_of(node: ast.expr) -> str | None:
    """`X` for both `X` and `something.X`, and `None` for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _raised_in(body: list[ast.stmt]) -> set[str]:
    """Every `ApiError` a block raises, by class name, called or bare, qualified or not."""
    found: set[str] = set()
    for statement in body:
        for node in ast.walk(statement):
            if isinstance(node, ast.Raise) and node.exc is not None:
                raised = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
                name = _name_of(raised)
                if name in ERRORS:
                    found.add(name)
    return found


def _named_in(value: ast.AST) -> set[str]:
    """The classes every `responses(...)` call inside `value` names."""
    return {
        name
        for node in ast.walk(value)
        if isinstance(node, ast.Call) and _name_of(node.func) == "responses"
        for argument in node.args
        if (name := _name_of(argument)) is not None
    }


def _route_decorator(decorator: ast.expr) -> ast.Call | None:
    """`decorator` when it is `<something>router.<verb>(...)`, and `None` otherwise."""
    if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
        return None
    owner = _name_of(decorator.func.value)
    return decorator if owner is not None and owner.endswith("router") else None


class Route(NamedTuple):
    module: str
    name: str
    raised: frozenset[str]
    declared: frozenset[str]


def _routes() -> list[Route]:
    found: list[Route] = []
    for path, node in _nodes():
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for decorator in node.decorator_list:
                route = _route_decorator(decorator)
                if route is not None:
                    declared = next(
                        (_named_in(one.value) for one in route.keywords if one.arg == "responses"),
                        set(),
                    )
                    found.append(
                        Route(
                            _module_of(path),
                            node.name,
                            frozenset(_raised_in(node.body)),
                            frozenset(declared),
                        )
                    )
    return found


ROUTES = _routes()


def test_the_walk_finds_every_route_the_app_serves_and_no_other() -> None:
    """The tests below pass against an empty walk. Compared with the handlers read off the
    running app, by module and name, so a walk that stops matching fails here instead."""
    served = sorted(
        (route.endpoint.__module__, route.endpoint.__name__)
        for _, route in endpoints_of(create_app())
    )

    assert sorted((route.module, route.name) for route in ROUTES) == served


def test_a_route_declares_every_refusal_it_raises() -> None:
    undeclared = [
        f"{route.module}.{route.name} raises {sorted(route.raised - route.declared)}"
        for route in ROUTES
        if route.raised - route.declared
    ]

    assert undeclared == [], (
        f"{undeclared}. Name each in responses(...) on the route's decorator, or openapi.json "
        f"does not describe it and the frontend cannot be typed against it."
    )


def test_no_refusal_is_declared_and_unreachable() -> None:
    declared: set[str] = set()
    for _, node in _nodes():
        declared |= _named_in(node)

    unreachable = sorted(set(ERRORS) - declared)

    assert unreachable == [], (
        f"{unreachable} are defined in app/errors.py and named by no route. Declare them where "
        f"they can happen, or delete them."
    )


def test_nothing_in_app_builds_an_http_exception() -> None:
    """A bare `HTTPException` is a refusal neither test above can see. `ApiError` subclasses it
    rather than building one, so nothing in `app/` is exempt."""
    bare = sorted(
        f"{path.relative_to(SOURCE.parent)}:{getattr(node, 'lineno', 0)}"
        for path, node in _nodes()
        if isinstance(node, ast.Call) and _name_of(node.func) == "HTTPException"
    )

    assert bare == [], (
        f"{bare} build an HTTPException directly. Declare the refusal as a class in "
        f"app/errors.py, raise that, and name it in responses(...) on the route."
    )


class _NoSuchItem(ApiError):
    status: ClassVar[int] = 404
    description: ClassVar[str] = "No such item"


class _NoSuchList(ApiError):
    status: ClassVar[int] = 404
    description: ClassVar[str] = "No such list"


class _ListArchived(ApiError):
    status: ClassVar[int] = 409
    description: ClassVar[str] = "That list is archived"


def test_refusals_sharing_a_status_are_one_declaration_naming_both() -> None:
    """OpenAPI holds one description per status, so a route that answers `404` for two reasons
    says both in it rather than losing one."""
    declared = responses(_NoSuchItem, _NoSuchList, _ListArchived)

    assert declared[404]["description"] == "No such item, or no such list"
    assert declared[409]["description"] == "That list is archived"


def test_every_refusal_in_app_errors_is_built_on_app_refusal() -> None:
    """`app/errors.py` is the project's and an update never touches it, so a project generated
    before `app/refusal.py` existed still defines its own `ApiError` there. Its refusals then
    pass every test above by being invisible to them."""
    own = sorted(
        name
        for name, value in vars(errors).items()
        if isinstance(value, type)
        and issubclass(value, HTTPException)
        and not issubclass(value, refusal.ApiError)
    )

    assert own == [], (
        f"app/errors.py defines {own} on its own HTTPException. Delete its ApiError, _joined and "
        f"responses, and import ApiError and responses from app.refusal instead."
    )
