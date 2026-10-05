"""The shapes the contract is made of. Change one and run `make openapi`.

Import every shape as `from app.models import X`. The modules are layered: `shared` imports
nothing, and `LAYERS` in `tests/models/layers.py` says what each other module may import.
`docs/adr/template/0007`.
"""

from app.models.client_events import (
    MAX_CLIENT_EVENTS as MAX_CLIENT_EVENTS,
    ClientEvent as ClientEvent,
    ClientEvents as ClientEvents,
    ErrorName as ErrorName,
    RequestId as RequestId,
    RouteId as RouteId,
    Status as Status,
)
from app.models.shared import ErrorBody as ErrorBody
from app.models.tasks import (
    CreateTaskBody as CreateTaskBody,
    Task as Task,
    UpdateTaskBody as UpdateTaskBody,
)
