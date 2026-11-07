from fastapi import APIRouter

from app.deps import JobsDep
from app.models import Job
from app.refusal import NoSuchJob, responses

router = APIRouter()


@router.get("/jobs/{id}", responses=responses(NoSuchJob))
async def read_job(jobs: JobsDep, id: str) -> Job:
    job = await jobs.read(id)
    if job is None:
        raise NoSuchJob
    return job
