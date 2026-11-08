"""What one `GET /reads` runs. The project owns this module: the template writes it once and an
update never touches it, so set your own numbers here.

`app/reads.py` is the mechanism, and a deployment may replace each number:
`wiring.build_reads`. `docs/adr/template/0029`.
"""

from typing import Final

from app.reads import Limits as Limits

LIMITS: Final = Limits(part_bound_seconds=0.5, max_parts=16, part_concurrency=4)
