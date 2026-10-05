"""`app/lifespan.py` reads no variable: what it runs comes from `app/wiring.py`."""

import ast
from pathlib import Path

from app import lifespan

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
