"""What a run left out, said out loud.

`norecursedirs` keeps each tier in `tiers.py` out of the default run, which is what lets every
test in this project be a test that runs rather than one that skips itself. The cost of that
is silence: `pytest -q` reports what it collected and has no way to mention what it never
looked at. This prints the tiers that were not in the run, so a green result never reads as
"everything passed" when a whole folder was not selected.
"""

from pathlib import Path

import pytest

from tests.tiers import python_tiers


def paths_asked_for(config: pytest.Config) -> list[Path]:
    """The arguments that name something on disk. A value that only looks like one -- the
    `integration` in `-k integration` -- selects no tier and must not silence its line."""
    named = (arg.split("::")[0] for arg in config.invocation_params.args)
    return [Path(one) for one in named if Path(one).exists()]


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter, config: pytest.Config
) -> None:
    asked_for = paths_asked_for(config)
    for tier in python_tiers():
        under_it = [one for one in asked_for if tier.folder in one.parts]
        if under_it:
            continue
        terminalreporter.write_line(
            f"not in this run: tests/{tier.folder}, which needs {tier.needs} -- "
            f"`{tier.runs}` runs it"
        )
