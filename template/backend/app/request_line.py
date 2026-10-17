"""The fields this project names on `request completed`, beyond the tenant and the actor. The
project owns this module: the template writes it once and an update never touches it.

A field declared here is on every line, null unless the request named it through
`app.log.name_on_request_line`. Declare an id such as `order.id`, never a name, an email or
anything a person typed.
`docs/adr/template/0011` and `docs/adr/template/0015`.
"""

from typing import Final

FIELDS: Final[frozenset[str]] = frozenset()
