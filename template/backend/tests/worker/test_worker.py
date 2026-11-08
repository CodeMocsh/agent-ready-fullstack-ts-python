"""The worker is a process of its own, and the web process never runs it.
`docs/adr/template/0027`."""

import os
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Final, override

import pytest

from app.loops import DatabaseGone
from app.store.budget import Draws
from app.store.jobs import Claim
from app.store.pg import PostgresDatabase
from app.store.pg_jobs import JobClaims
from app.worker import work

BACKEND: Final = Path(__file__).resolve().parents[2]

ONE_PROCESS: Final = Draws(instances=1, in_process={}, outside={})


def _python(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
    unset = {"DATABASE_URL", "DATABASE_OWNER_URL", "DATABASE_INSTANCES"}
    return subprocess.run(
        [sys.executable, *args],
        cwd=BACKEND,
        env={**{k: v for k, v in os.environ.items() if k not in unset}, **env},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_the_web_process_loads_no_job_loop() -> None:
    loaded = _python(
        "-c",
        "import sys\n"
        "from app.main import module_app\n"
        "module_app()\n"
        "print(sorted({'app.worker', 'app.store.pg_jobs'} & set(sys.modules)))",
    )

    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.strip() == "[]"


def test_the_worker_refuses_the_memory_substrate() -> None:
    started = _python("-m", "app.worker", APP_ENV="development")

    assert started.returncode != 0
    assert "WorkerNeedsPostgres" in started.stderr


class GoneClaims(JobClaims):
    """Claims whose database is gone: each loop of the worker raises `DatabaseGone` at once."""

    @override
    async def listen(self, woken: Callable[[], None]) -> None:
        raise DatabaseGone("the database is gone")

    @override
    async def claim(self, kinds: Iterable[str]) -> Claim | None:
        raise DatabaseGone("the database is gone")


async def test_a_worker_whose_database_is_gone_stops_with_database_gone() -> None:
    database = PostgresDatabase(
        dsn="postgresql://nowhere.invalid/app", draws=ONE_PROCESS, kinds=lambda _: {}
    )

    with pytest.raises(DatabaseGone, match="the database is gone"):
        await work(GoneClaims(database), at_once=2)
