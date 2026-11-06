"""What this project holds open to Postgres beside the template's pool. The project owns this
module: the template writes it once and an update never touches it, so add your own here.

Each process gets an equal share of the server's connections. The template's pool takes what
remains of that share after `IN_PROCESS`. `app/store/budget.py` is the mechanism, and
`docs/adr/template/0022` says how the share is calculated.
"""

from collections.abc import Mapping
from typing import Final

IN_PROCESS: Final[Mapping[str, int]] = {}
"""Each pool or connection that one process holds beside the template's pool, by name, and the
most connections it holds at once. Size the pool from its entry here."""

OUTSIDE: Final[Mapping[str, int]] = {"the release step": 1}
"""Each holder that is not one of the processes, by name, and the most connections it holds at
once: the release step, a worker that runs as a service of its own, an operator's session."""
