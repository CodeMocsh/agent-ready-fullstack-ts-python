"""Each loop that runs until its process stops runs through `app.loops.rounds`, so each one waits
for the database within the same bound. A `while True` with no `break` and no `return` is such a
loop. `docs/adr/template/0027`."""

import ast
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Final

APP: Final = Path(__file__).resolve().parents[2] / "app"

RUNS_ITS_OWN_LOOP: Final = {
    "loops.py": "rounds is the loop each other loop runs through",
    "pacing.py": "every waits one period at a time, for the loop that iterates it",
}
"""Each module under `app/` that may hold a loop with no exit, and why."""


def _exits(nodes: Iterable[ast.AST], *, inside_a_loop: bool) -> bool:
    for node in nodes:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
            continue
        if isinstance(node, ast.Return) or (isinstance(node, ast.Break) and not inside_a_loop):
            return True
        if isinstance(node, ast.While | ast.For | ast.AsyncFor):
            if _exits(node.body, inside_a_loop=True) or _exits(
                node.orelse, inside_a_loop=inside_a_loop
            ):
                return True
        elif _exits(ast.iter_child_nodes(node), inside_a_loop=inside_a_loop):
            return True
    return False


def loops_with_no_exit(source: str) -> Iterator[int]:
    """The line of each `while True` in `source` with no `break` of its own and no `return`."""
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.While)
            and isinstance(node.test, ast.Constant)
            and node.test.value is True
            and not _exits(node.body, inside_a_loop=False)
        ):
            yield node.lineno


def test_each_loop_with_no_exit_runs_through_rounds() -> None:
    found = [
        f"app/{path.relative_to(APP)}:{line}"
        for path in sorted(APP.rglob("*.py"))
        if str(path.relative_to(APP)) not in RUNS_ITS_OWN_LOOP
        for line in loops_with_no_exit(path.read_text(encoding="utf-8"))
    ]

    assert found == [], (
        "a loop that runs until its process stops dies on the first database outage. Write "
        "one round as a function, and run it with app.loops.rounds(name, each_round)."
    )


def test_a_loop_with_no_exit_is_found() -> None:
    source = (
        "async def draw():\n"
        "    while True:\n"
        "        try:\n"
        "            await drawn()\n"
        "        except Exception:\n"
        "            continue\n"
    )

    assert list(loops_with_no_exit(source)) == [2]


def test_a_break_of_an_inner_loop_is_no_exit_of_the_outer_one() -> None:
    source = "while True:\n    while True:\n        break\n    def inner():\n        return 1\n"

    assert list(loops_with_no_exit(source)) == [1]


def test_a_loop_that_breaks_or_returns_has_an_exit() -> None:
    source = (
        "def pages():\n"
        "    while True:\n"
        "        if done():\n"
        "            break\n"
        "    while True:\n"
        "        for page in each():\n"
        "            return page\n"
        "    while not stopped:\n"
        "        pass\n"
    )

    assert list(loops_with_no_exit(source)) == []
