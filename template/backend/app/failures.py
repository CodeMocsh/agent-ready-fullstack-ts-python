"""Every way this project's work fails that a person reads, once each: a class carrying its own
cause and sentence.

The project owns this module: the template writes it once and an update never touches it, so
add your failures here. Raise one where the work fails. `app/failing.py` is the mechanism.
`docs/adr/template/0031` and `docs/adr/template/0015`.

`STILL_FREE_TEXT` names each response field, as `Schema.property`, that holds a failure as free
text today. `tests/failures/test_failure_fields.py` passes each one listed, and fails on one
that is not listed or no longer holds free text. The list only shrinks.
"""

from typing import Final

from app.failing import Failing as Failing

STILL_FREE_TEXT: Final[frozenset[str]] = frozenset()
