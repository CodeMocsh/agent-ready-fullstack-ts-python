from typing import Final, Literal

from pydantic import BaseModel, Field, JsonValue

from app.models.shared import ID_MAX, MAX_BYTES, NAME_MAX

JsonObject = dict[str, JsonValue]
"""The input of a job, and the outcome of a job that is done."""

JSON_BYTES: Final = 256 * 1024
"""The most bytes a job's input or outcome takes as JSON, and the bound on each column that holds
one."""

JobState = Literal["pending", "done", "dead"]
"""`pending` until a worker ends the job. `done` with an outcome, or `dead` with a failure."""


class Job(BaseModel):
    """One job, as the tenant that asked for it reads it. `outcome` is set when the job is `done`,
    and `failure` names why it is `dead`."""

    id: str = Field(max_length=ID_MAX)
    kind: str = Field(max_length=NAME_MAX)
    state: JobState
    outcome: JsonObject | None = Field(json_schema_extra={MAX_BYTES: JSON_BYTES})
    failure: str | None = Field(max_length=NAME_MAX)
