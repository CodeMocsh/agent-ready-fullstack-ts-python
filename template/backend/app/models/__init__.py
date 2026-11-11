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
from app.models.jobs import (
    JSON_BYTES as JSON_BYTES,
    Job as Job,
    JobState as JobState,
    JsonObject as JsonObject,
)
from app.models.reads import (
    AnsweredPart as AnsweredPart,
    LeftBecause as LeftBecause,
    LeftPart as LeftPart,
    Reads as Reads,
)
from app.models.shared import (
    ID_MAX as ID_MAX,
    MAX_BYTES as MAX_BYTES,
    NAME_MAX as NAME_MAX,
    PAGE_BYTES as PAGE_BYTES,
    PAGE_MAX as PAGE_MAX,
    ErrorBody as ErrorBody,
)
from app.models.tasks import (
    TITLE_MAX as TITLE_MAX,
    CreateTaskBody as CreateTaskBody,
    Task as Task,
    TaskPage as TaskPage,
    UpdateTaskBody as UpdateTaskBody,
)
