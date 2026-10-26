"""`app/lifespan.py` reads no variable: what it runs comes from `app/wiring.py`. It builds the
routes before the first request."""

import ast
from pathlib import Path

import pytest
from fastapi import routing
from fastapi.testclient import TestClient

from app import lifespan, main

READERS = frozenset({"os", "environ", "getenv"})


def test_the_lifespan_reads_no_variable() -> None:
    tree = ast.parse(Path(lifespan.__file__).read_text())
    named = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    named |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    imported |= {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert (named | imported) & READERS == set(), (
        "app/lifespan.py reads the environment. Read it in app/wiring.py and pass what it "
        "builds in."
    )


def _refused(**named: object) -> None:
    raise AssertionError(f"a request built the route at {named['path']}")


def test_the_routes_are_built_before_the_first_request(monkeypatch: pytest.MonkeyPatch) -> None:
    with TestClient(main.create_app()) as serving:
        monkeypatch.setattr(routing, "get_dependant", _refused)

        assert serving.get("/health").status_code == 200


def test_a_request_to_an_app_whose_lifespan_never_ran_builds_a_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routing, "get_dependant", _refused)

    with pytest.raises(AssertionError, match="a request built the route"):
        TestClient(main.create_app()).get("/health")
