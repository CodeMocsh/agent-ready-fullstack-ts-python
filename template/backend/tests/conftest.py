"""What a run left out, said out loud.

`norecursedirs` keeps each tier in `tiers.py` out of the default run, which is what lets every
test in this project be a test that runs rather than one that skips itself. The cost of that
is silence: `pytest -q` reports what it collected and has no way to mention what it never
looked at. This prints the tiers that were not in the run, so a green result never reads as
"everything passed" when a whole folder was not selected.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.wiring import NEEDS_THE_ENDPOINT, NOT_READ, OTLP_ENDPOINT_ENV
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


@pytest.fixture(autouse=True)
def no_collector(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test inherits a Collector from the shell -- `make observe` prints exports that would
    otherwise instrument every app the suite builds."""
    for name in (OTLP_ENDPOINT_ENV, "OTEL_EXPORTER_OTLP_PROTOCOL", *NEEDS_THE_ENDPOINT, *NOT_READ):
        monkeypatch.delenv(name, raising=False)


Logged = Callable[[], list[dict[str, Any]]]


@pytest.fixture
def logged(capsys: pytest.CaptureFixture[str]) -> Logged:
    """What this process wrote to stdout since the last read, one parsed line per record.

    Parsed rather than searched, so a line that is not one JSON object fails the test that
    produced it. `app.log.configure` binds stdout when `create_app` runs, so build the app
    inside the test that reads it.
    """

    def read() -> list[dict[str, Any]]:
        return [json.loads(line) for line in capsys.readouterr().out.splitlines()]

    return read
