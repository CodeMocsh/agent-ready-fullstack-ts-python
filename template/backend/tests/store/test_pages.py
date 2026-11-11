"""Only `app/store/pages.py` reads many rows, so every such read is one bounded page.
`docs/adr/template/0026`."""

import ast
from pathlib import Path
from typing import Final

import pytest

from app.store.pages import UnorderedPage, fetch_page

APP: Final = Path(__file__).resolve().parents[2] / "app"

MANY_ROWS: Final = frozenset({"fetch", "cursor", "copy_from_query", "copy_from_table"})
"""The asyncpg calls that answer more than one row."""

ELSEWHERE: Final[dict[str, str]] = {
    "store/pages.py": "the one read of a page",
    "store/budget.py": "reads one row of pg_settings for each setting it names",
}
"""Each module outside `fetch_page` that may read many rows, and why its rows are bounded."""


def reads_of_many_rows(path: Path) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in MANY_ROWS
    ]


def test_only_fetch_page_reads_many_rows() -> None:
    found = {
        f"{path.relative_to(APP)}:{line}"
        for path in sorted(APP.rglob("*.py"))
        if str(path.relative_to(APP)) not in ELSEWHERE
        for line in reads_of_many_rows(path)
    }

    assert found == set(), (
        f"{sorted(found)} read many rows. Read a page with app.store.pages.fetch_page, or say in "
        f"ELSEWHERE why the rows are bounded."
    )


def test_every_module_allowed_elsewhere_still_reads_many_rows() -> None:
    assert [name for name in ELSEWHERE if not reads_of_many_rows(APP / name)] == []


@pytest.mark.parametrize("order", ["seq, id", "seq ASC", "lower(title)", "seq; DROP TABLE x"])
async def test_a_page_in_any_order_but_one_column_is_refused(order: str) -> None:
    with pytest.raises(UnorderedPage):
        await fetch_page(None, "SELECT seq FROM tasks", order)
