import subprocess
from pathlib import Path

from tests.test_gate import HOLD, a_clone_with_a_gate, run_the_gate, times_the_gate_ran
from tests.test_gate_wait import line_from, running


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
