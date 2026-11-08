"""The GitHub workflow runs the full suite, including the commit checks.

This module ships only with the workflow: a project that answers `github_ci=false` gets neither.
"""

from pathlib import Path

from tests.test_gate import ROOT, without_comments

WORKFLOWS = ROOT / ".github" / "workflows"


def workflows() -> list[tuple[Path, str]]:
    """Every workflow this project ships, with its comment lines stripped. A workflow that
    named the gate in a comment and ran `true` would satisfy a plain substring check."""
    found = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    return [(path, without_comments(path.read_text(encoding="utf-8"))) for path in found]


def test_a_workflow_runs_the_suite_rather_than_a_copy_of_it():
    """The workflow calls the full suite target instead of copying its commands."""
    shipped = workflows()

    assert shipped, (
        "no workflow ships, so this test loops over nothing and passes without checking "
        "anything -- the shape a check takes when it has quietly stopped being one. Restore "
        "`.github/workflows/ci.yml`, or, if this project runs its checks somewhere GitHub "
        "cannot see, delete `.github/` and this file together."
    )

    for path, body in shipped:
        assert "make suite" in body, (
            f"{path.relative_to(ROOT)} does not run `make suite`. A workflow that "
            f"re-lists the full suite's steps is a second copy of the full suite, and the copy is what "
            f"goes stale -- point it at the target, or add a target for the part it runs."
        )
