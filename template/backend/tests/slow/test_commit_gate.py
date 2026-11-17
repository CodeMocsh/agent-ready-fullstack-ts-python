import shlex
from pathlib import Path

import pytest

from tests.test_gate import (
    PYPROJECT,
    a_clone_with_a_gate,
    recipe_of,
    run_the_gate,
)


@pytest.mark.parametrize("half", ["frontend", "backend"])
def test_a_partial_install_runs_only_its_half_and_records_no_pass(
    tmp_path: Path, half: str
) -> None:
    clone = a_clone_with_a_gate(tmp_path, "exit 1")
    absent = clone / ("backend/.venv" if half == "frontend" else "frontend/node_modules")
    absent.rmdir()
    (clone / "devtools/secrets.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    makefile = clone / "Makefile"
    makefile.write_text(
        makefile.read_text()
        + "\n".join(
            f"test-commit-{name}:\n\t@echo selected-{name}" for name in ("frontend", "backend")
        )
        + "\n",
        encoding="utf-8",
    )
    result = run_the_gate(clone)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"selected-{half}"
    assert "PARTIAL RUN" in result.stderr
    assert "full suite      NOT RUN" in result.stderr
    assert "s on the clock of a 600s budget" in result.stderr
    assert not (clone / ".git/gate-commit-passed").exists()


def test_slow_tests_fail_the_full_run_and_leave_the_commit_run(pytester: pytest.Pytester) -> None:
    pytester.makepyprojecttoml(PYPROJECT.read_text())
    for name in (
        "test_fast.py",
        "area/test_nearby.py",
        "area/slower/test_ordinary.py",
        "slow/test_root.py",
        "area/slow/test_nested.py",
        "area/slow/deep/test_deep.py",
        "started/test_started.py",
    ):
        path = pytester.path / "tests" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        passes = "slow" not in path.relative_to(pytester.path).parts and "started" not in name
        path.write_text(f"def test_selection():\n    assert {passes}\n", encoding="utf-8")
    command = shlex.split(recipe_of("test-commit-backend")[0])
    commit = pytester.runpytest_subprocess(*command[command.index("pytest") + 1 :])
    commit.assert_outcomes(passed=3)
    full_command = next(shlex.split(line) for line in recipe_of("test-fast") if "pytest" in line)
    full = pytester.runpytest_subprocess(*full_command[full_command.index("pytest") + 1 :])
    full.assert_outcomes(passed=3, failed=4)
