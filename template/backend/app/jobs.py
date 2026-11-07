"""Each kind of job this project runs, and its handler. The project owns this module: the
template writes it once and an update never touches it, so add your own kinds here.

A request asks for a job with `Database.jobs(tenant_id).ask(kind, input)` and answers `202`.
On Postgres the worker process, `python -m app.worker`, runs it. The memory substrate runs it in
the process that asked. `app/store/jobs.py` is the mechanism, and `docs/adr/template/0027` says
why.

A handler gets the store of the job's tenant and the job's input, and returns the outcome. It
runs at least once, so a second run on the same input does what the first did. A handler that
raises is tried again, up to `MAX_ATTEMPTS` attempts.
"""

from collections.abc import Mapping
from typing import Final

from app.models import JsonObject, UpdateTaskBody
from app.store import TaskStore
from app.store.jobs import Handler

CLOSE_A_TASK: Final = "close a task"


async def close_a_task(store: TaskStore, input: JsonObject) -> JsonObject:
    """Mark the task `input["id"]` done. The outcome says whether the task was there."""
    id = input["id"]
    if not isinstance(id, str):
        raise TypeError(f"the id of a task is a string, and this job holds {type(id).__name__}")
    closed = await store.update(id, UpdateTaskBody(done=True))
    return {"closed": closed is not None}


KINDS: Final[Mapping[str, Handler]] = {CLOSE_A_TASK: close_a_task}
"""Each kind of job, and the handler that runs it."""
