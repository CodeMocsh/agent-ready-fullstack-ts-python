"""The one read of many rows: a page, picked and measured in SQL. `docs/adr/template/0026`.

A page holds at most `PAGE_MAX` rows and at most `PAGE_BYTES` bytes, and always its first row.
Postgres stops at whichever bound comes first, so no read ships more than one page, however wide
or many the rows. Each column's own bound keeps a single row under `PAGE_BYTES`, so the first
row fits too.

A row's bytes are the length of its text, `octet_length(row::text)`: what Postgres sends for it,
give or take the quoting.
"""

import re
from dataclasses import dataclass
from typing import Any, Final

from app import log
from app.models import PAGE_BYTES, PAGE_MAX

_ORDER: Final = re.compile(r"^[a-z_][a-z0-9_]*( DESC)?$")


class UnorderedPage(ValueError):
    """A page was asked for in an order that is not one column, ascending or `DESC`."""


@dataclass(frozen=True)
class Page:
    """The rows of one page, in order, and whether a page after it holds more."""

    rows: list[Any]
    more: bool


async def fetch_page(conn: Any, query: str, order: str, *args: Any) -> Page:
    """The first page of the rows `query` selects, in `order`.

    `query` is a `SELECT` with no `ORDER BY` and no `LIMIT`, and selects the column `order`
    names. `args` are its parameters. `order` is one column, which must be unique among the rows,
    with an optional ` DESC`. Raises `UnorderedPage` on any other `order`. Counts one page read
    on this request's `request completed` line."""
    if _ORDER.match(order) is None:
        raise UnorderedPage(f"a page is ordered by one column, not by {order!r}")
    rows = await conn.fetch(
        f"SELECT * FROM ("
        f"SELECT p.*, row_number() OVER w AS page_row, count(*) OVER () AS page_fetched, "
        f"sum(octet_length(p::text)) OVER w AS page_bytes "
        f"FROM ({query} ORDER BY {order} LIMIT ${len(args) + 1}) AS p "
        f"WINDOW w AS (ORDER BY {order} ROWS UNBOUNDED PRECEDING)"
        f") AS measured WHERE page_row = 1 OR page_bytes <= ${len(args) + 2} ORDER BY {order}",
        *args,
        PAGE_MAX + 1,
        PAGE_BYTES,
    )
    log.page_read()
    page = rows[:PAGE_MAX]
    return Page(rows=page, more=bool(rows) and rows[0]["page_fetched"] > len(page))
