"""The fields this project names on `request completed`, beyond `tenant_id`. The project owns
this module: the template writes it once and an update never touches it.

A field declared here is on every line, null unless the request named it through
`app.log.name_on_request_line` -- from the identity seam, once it resolves more than a tenant.
Declare an id such as `user.id`, never a name, an email or anything a person typed.
`docs/adr/template/0009` and `docs/adr/template/0017`.
"""

from typing import Final

FIELDS: Final[frozenset[str]] = frozenset()
