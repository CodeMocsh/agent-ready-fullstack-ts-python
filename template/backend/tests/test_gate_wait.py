import os
import select
import signal
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

import pytest

from tests.test_gate import HOLD, a_clone_with_a_gate, run_the_gate, times_the_gate_ran


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


def test_a_waiter_reuses_the_pass_written_by_the_holder(tmp_path: Path) -> None:
    clone = a_clone_with_a_gate(tmp_path, "echo ran >> ran.log; echo running; read answer")
    with running(["sh", "devtools/gate.sh"], clone, subprocess.PIPE) as first:
        assert line_from(first.stdout) == "running\n"
        with running(["sh", "devtools/gate.sh"], clone) as second:
            assert "waiting for the gate" in line_from(second.stderr)
            _, first_error = first.communicate(input="continue\n", timeout=30)
            _, second_error = second.communicate(timeout=30)
    assert first.returncode == 0, first_error
    assert second.returncode == 0, second_error
    assert "already passed the commit gate" in second_error
    assert times_the_gate_ran(clone) == 1


def test_the_pass_names_the_tree_read_after_the_wait(tmp_path: Path) -> None:
    clone = a_clone_with_a_gate(tmp_path, "echo ran >> ran.log")
    command = [
        "sh",
        "-c",
        'exec 9>>"$1"; perl "$2" "$1"; echo held; read answer',
        "sh",
        str(tmp_path / "gate.lock"),
        str(HOLD),
    ]
    with running(command, clone, subprocess.PIPE) as holder:
        assert line_from(holder.stdout) == "held\n"
        with running(["sh", "devtools/gate.sh"], clone) as waiter:
            assert "waiting for the gate" in line_from(waiter.stderr)
            (clone / "kept.txt").write_text("edited while waiting\n", encoding="utf-8")
            holder.communicate(input="continue\n", timeout=30)
            _, error = waiter.communicate(timeout=30)
    assert waiter.returncode == 0, error
    again = run_the_gate(clone)
    assert again.returncode == 0, again.stderr
    assert "already passed the commit gate" in again.stderr
    assert times_the_gate_ran(clone) == 1


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


def test_a_passed_tree_does_not_wait_for_another_gate(tmp_path: Path) -> None:
    clone = a_clone_with_a_gate(tmp_path, "echo ran >> ran.log")
    assert run_the_gate(clone).returncode == 0
    command = [
        "sh",
        "-c",
        'exec 9>>"$1"; perl "$2" "$1"; echo held; read answer',
        "sh",
        str(tmp_path / "gate.lock"),
        str(HOLD),
    ]
    with running(command, clone, subprocess.PIPE) as holder:
        assert line_from(holder.stdout) == "held\n"
        with running(["sh", "devtools/gate.sh"], clone) as repeated:
            _, error = repeated.communicate(timeout=10)
            assert repeated.returncode == 0, error
            assert "already passed the commit gate" in error
        holder.communicate(input="continue\n", timeout=30)
    assert times_the_gate_ran(clone) == 1
