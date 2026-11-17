import os
import select
import signal
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

import pytest

from tests.test_gate import a_clone_with_a_gate


@contextmanager
def running(
    command: list[str], clone: Path, stdin: int = subprocess.DEVNULL
) -> Generator[subprocess.Popen[str]]:
    environment = {**os.environ, "PATH": f"{clone.parent / 'bin'}{os.pathsep}{os.environ['PATH']}"}
    with subprocess.Popen(
        command,
        cwd=clone,
        env=environment,
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    ) as process:
        try:
            yield process
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=30)


def line_from(stream: IO[str] | None) -> str:
    assert stream is not None
    ready, _, _ = select.select([stream], [], [], 30)
    assert ready, "gate process produced no line within 30 seconds"
    return stream.readline()


def test_an_assertion_failure_stops_the_process_waiting_for_input(tmp_path: Path) -> None:
    clone = a_clone_with_a_gate(tmp_path, "echo ran >> ran.log")
    started: list[subprocess.Popen[str]] = []
    with (
        pytest.raises(AssertionError, match="planted failure"),
        running(["sh", "-c", "echo ready; read answer"], clone, subprocess.PIPE) as process,
    ):
        started.append(process)
        assert line_from(process.stdout) == "ready\n"
        raise AssertionError("planted failure")
    assert started[0].returncode == -signal.SIGKILL
