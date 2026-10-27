"""What every suite here is handed, and what a run left out.

**The development loop is set before anything imports the app**, so a module that reads
`APP_ENV` at import reads `development`.

**A step that uses more than `HELD_SECONDS` of CPU on the reference core, on an event loop, is an
error at teardown of its test.** `tests/event_loop.py` names the reference core. At import, this
module times a fixed workload on this machine, once per process, and scales the limit to it. So
a step that passes here passes on the reference core. asyncio runs in debug mode on every event
loop here: the one `TestClient` serves the app on, and the one pytest-asyncio runs a test on.
Any other record asyncio logs from WARNING up is an error too, except its line that a step took
long in wall time. A record logged between two tests, by a fixture wider than one test, is an
error at teardown of the next test, or of the last one.

**What a run left out, said out loud.** `norecursedirs` keeps each tier in `tiers.py` out of the default run, which is what lets every
test in this project be a test that runs rather than one that skips itself. The cost of that
is silence: `pytest -q` reports what it collected and has no way to mention what it never
looked at. `pytest_terminal_summary` prints the tiers that were not in the run, so a green result never reads as
"everything passed" when a whole folder was not selected.
"""

import json
import os
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import pytest

from app.deployment import (
    DATABASE_URL_ENV,
    DEVELOPMENT,
    ENVIRONMENT_ENV,
    TELEMETRY_ENV,
    TIMEOUTS_ENV,
)

os.environ[ENVIRONMENT_ENV] = DEVELOPMENT
os.environ["PYTHONASYNCIODEBUG"] = "1"

from app.migrate import OWNER_URL_ENV
from app.store.conn import SCHEMA_ENV
from tests.event_loop import (
    Reported,
    StepLimit,
    measure_every_step,
    reported,
    step_limit_on_this_machine,
)
from tests.tiers import python_tiers

STEP_LIMIT = step_limit_on_this_machine()
measure_every_step(STEP_LIMIT)

pytest_plugins = ["pytester"]


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


@pytest.fixture(scope="session")
def step_limit() -> StepLimit:
    """The limit every step on an event loop in this process is held to."""
    return STEP_LIMIT


@pytest.fixture(scope="session", autouse=True)
def asyncio_logged() -> Generator[Reported]:
    """Every record asyncio logs in this session. Checked once more after the last test."""
    with reported() as said:
        yield said
        said.check()


@pytest.fixture(autouse=True)
def asyncio_reports(asyncio_logged: Reported) -> Generator[Reported]:
    """What asyncio logged since the test before this one. Checked at teardown."""
    yield asyncio_logged
    asyncio_logged.check()


@pytest.fixture(autouse=True)
def nothing_the_shell_exports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts with no database, telemetry or timeout variable set, whatever the shell
    exports, so the app it builds runs on the in-memory substrate. A test that wants Postgres
    sets `DATABASE_URL` and `DB_SCHEMA` itself."""
    for name in (DATABASE_URL_ENV, OWNER_URL_ENV, SCHEMA_ENV, *TELEMETRY_ENV, *TIMEOUTS_ENV):
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
