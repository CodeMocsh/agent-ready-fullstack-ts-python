"""The GitHub workflow runs the gate the git hook runs, and no copy of it.

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


def test_a_workflow_runs_the_gate_rather_than_a_copy_of_it():
    """A workflow that runs its own list of steps drifts from `make gate` silently, in
    the direction of checking less, and the drift shows up as a green push that a commit would
    have refused. Run the target instead. If it needs to run only part of the gate, make that
    part a target too.

    The body is read with comments stripped, so a workflow naming the target in a comment and
    running `true` does not satisfy this. Neither does naming `check_template.sh`, which an
    earlier version accepted: that is a script in the generator repository and not a file this
    project contains, so it could never be the right answer here and only bought a pass."""
    shipped = workflows()

    assert shipped, (
        "no workflow ships, so this test loops over nothing and passes without checking "
        "anything -- the shape a check takes when it has quietly stopped being one. Restore "
        "`.github/workflows/ci.yml`, or, if this project runs its checks somewhere GitHub "
        "cannot see, delete `.github/` and this file together."
    )

    for path, body in shipped:
        assert "make gate" in body, (
            f"{path.relative_to(ROOT)} does not run `make gate`. A workflow that "
            f"re-lists the gate's steps is a second copy of the gate, and the copy is what "
            f"goes stale -- point it at the target, or add a target for the part it runs."
        )
