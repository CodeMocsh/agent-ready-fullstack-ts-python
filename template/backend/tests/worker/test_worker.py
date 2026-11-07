"""The worker is a process of its own, and the web process never runs it.
`docs/adr/template/0027`."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Final

BACKEND: Final = Path(__file__).resolve().parents[2]


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
