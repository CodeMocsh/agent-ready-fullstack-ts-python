from fastapi import APIRouter

from app.deps import JobsDep
from app.models import Job
from app.reads import PART
from app.refusal import NoSuchJob, responses

router = APIRouter()


@router.get("/jobs/{id}", responses=responses(NoSuchJob), openapi_extra=PART)
async def read_job(jobs: JobsDep, id: str) -> Job:
    job = await jobs.read(id)
    if job is None:
        raise NoSuchJob
    return job
