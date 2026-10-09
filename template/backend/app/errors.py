"""Every way this API says no, once each: a class carrying its own status and sentence.

The project owns this module: the template writes it once and an update never touches it, so
add your refusals here. Raise one, and name it in `responses(...)` on the route.
`app/refusal.py` is the mechanism. `docs/adr/template/0015`.
"""

from typing import final

from app.refusal import ApiError as ApiError, responses as responses


@final
class NoSuchTask(ApiError):
    status = 404
    description = "Task not found"
