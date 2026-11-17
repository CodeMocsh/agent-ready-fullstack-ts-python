import os
import subprocess
from pathlib import Path

from tests.test_gate import (
    HOLD,
    WORKTREE_TREE,
    a_clone_with_a_gate,
    run_the_gate,
    times_the_gate_ran,
)


def test_a_gate_that_waited_its_turn_is_not_charged_for_the_wait(tmp_path: Path):
    """A gate that waits for the lock spends none of its budget on the wait."""
    import pytest

    clone = a_clone_with_a_gate(tmp_path, "echo ran >> ran.log", budget=1)
    lock = tmp_path / "gate.lock"
    holder = subprocess.Popen(
        ["sh", "-c", f'exec 9>>"$1"; perl {HOLD} "$1"; echo held; read _', "sh", str(lock)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None and holder.stdin is not None
    assert holder.stdout.readline() == "held\n"
    tools = clone.parent / "bin"
    waiting = subprocess.Popen(
        ["sh", "devtools/gate.sh"],
        cwd=clone,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}"},
    )
    with pytest.raises(subprocess.TimeoutExpired):
        waiting.wait(timeout=2)
    holder.stdin.close()
    holder.wait(timeout=10)
    _, err = waiting.communicate(timeout=30)

    assert "waiting for the gate already running" in err
    assert waiting.returncode == 0, err


def test_a_green_gate_is_not_run_again_over_the_same_tree(tmp_path: Path):
    clone = a_clone_with_a_gate(tmp_path, "echo ran >> ran.log")

    first = run_the_gate(clone)
    second = run_the_gate(clone)

    assert first.returncode == 0 and second.returncode == 0, first.stderr + second.stderr
    assert times_the_gate_ran(clone) == 1
    assert "already passed the commit gate" in second.stderr


def test_a_green_run_over_budget_fails_and_is_still_remembered_as_passed(tmp_path: Path):
    """Over budget is a regression to find, not a reason to run the same tree again: the retried
    commit of that tree skips the gate."""
    clone = a_clone_with_a_gate(tmp_path, "echo ran >> ran.log", budget=-1)

    first = run_the_gate(clone)
    second = run_the_gate(clone)

    assert first.returncode != 0
    assert "Over budget" in first.stderr
    assert second.returncode == 0, second.stderr
    assert times_the_gate_ran(clone) == 1


def test_a_pass_is_remembered_for_exactly_the_tree_the_gate_read(tmp_path: Path):
    """A recorded pass skips the gate, so any tree it matches that the gate did not read is a way
    past it: an edit not yet staged, a file not yet added. What git ignores is not the gate's."""

    def run(*command: str) -> str:
        done = subprocess.run(command, cwd=tmp_path, check=True, capture_output=True, text=True)
        return done.stdout

    def tree() -> str:
        return run("sh", str(WORKTREE_TREE)).strip()

    run("git", "init", "-q")
    (tmp_path / ".gitignore").write_text("ignored/\n")
    (tmp_path / "kept.py").write_text("one\n")
    run("git", "add", "kept.py")
    index = run("git", "ls-files", "--stage")

    fingerprint = tree()
    assert tree() == fingerprint

    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored" / "build.log").write_text("noise\n")
    assert tree() == fingerprint

    (tmp_path / "kept.py").write_text("two\n")
    edited = tree()
    assert edited != fingerprint

    (tmp_path / "added.py").write_text("")
    assert tree() not in {fingerprint, edited}

    assert run("git", "ls-files", "--stage") == index, "fingerprinting the tree staged something"


def test_a_red_gate_is_remembered_as_nothing(tmp_path: Path):
    clone = a_clone_with_a_gate(tmp_path, "echo ran >> ran.log; exit 1")

    first = run_the_gate(clone)
    second = run_the_gate(clone)

    assert first.returncode != 0 and second.returncode != 0
    assert times_the_gate_ran(clone) == 2


def test_a_second_gate_waits_for_the_first_and_says_where_it_runs(tmp_path: Path):
    """Two gates at once oversubscribe every core twice, and each then fails the other's
    timeouts. The second waits, and says whose turn it is rather than hanging in silence."""
    import pytest

    lock = tmp_path / "gate.lock"
    hold_the_lock = f'exec 9>>"$1"; perl {HOLD} "$1"; echo held; read _'

    def gate(checkout: Path, stdin: int) -> "subprocess.Popen[str]":
        checkout.mkdir()
        return subprocess.Popen(
            ["sh", "-c", hold_the_lock, "sh", str(lock)],
            cwd=checkout,
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    first_checkout = (tmp_path / "first").resolve()
    first = gate(first_checkout, subprocess.PIPE)
    assert first.stdout is not None and first.stdin is not None
    assert first.stdout.readline() == "held\n"

    second_checkout = (tmp_path / "second").resolve()
    second = gate(second_checkout, subprocess.DEVNULL)
    with pytest.raises(subprocess.TimeoutExpired):
        second.wait(timeout=0.5)
    assert lock.read_text() == f"{first_checkout}\n"

    first.stdin.close()
    first.wait(timeout=10)
    out, err = second.communicate(timeout=10)

    assert out == "held\n"
    assert err == f"pre-commit: waiting for the gate already running in {first_checkout}\n"
    assert lock.read_text() == f"{second_checkout}\n"


def test_an_edit_after_a_green_gate_runs_it_again(tmp_path: Path):
    clone = a_clone_with_a_gate(tmp_path, "echo ran >> ran.log")

    run_the_gate(clone)
    (clone / "kept.txt").write_text("two\n", encoding="utf-8")
    again = run_the_gate(clone)

    assert again.returncode == 0, again.stderr
    assert times_the_gate_ran(clone) == 2
