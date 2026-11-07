"""The example resource's one kind of job: close a task. `example_resource` covers this module
with the example, and `app/jobs.py` names its kind."""

from typing import Final

from app.models import JsonObject, UpdateTaskBody
from app.store import TaskStore

CLOSE_A_TASK: Final = "close a task"


async def close_a_task(store: TaskStore, input: JsonObject) -> JsonObject:
    """Mark the task `input["id"]` done. The outcome says whether the task was there."""
    id = input["id"]
    if not isinstance(id, str):
        raise TypeError(f"the id of a task is a string, and this job holds {type(id).__name__}")
    closed = await store.update(id, UpdateTaskBody(done=True))
    return {"closed": closed is not None}
