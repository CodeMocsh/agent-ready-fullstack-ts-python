"""The log lines this project writes, beyond the template's: one function per line, and its
parameters the only fields the line may carry. The project owns this module: the template
writes it once and an update never touches it.

`app/log.py` is the mechanism, and every line written here reaches stdout through it. Write
through `_LOG`, which it hears at `INFO`; a logger by any other name is heard only at `WARNING`
and above. Name a value by an id, never by a name, an email or anything a person typed.
`docs/adr/template/0009` and `docs/adr/template/0017`.
"""

from typing import Final

import structlog

_LOG: Final = structlog.stdlib.get_logger("app")
