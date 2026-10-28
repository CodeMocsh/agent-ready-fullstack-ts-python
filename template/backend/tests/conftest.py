"""What every suite here is handed, and what a run left out.

**The development loop is set before anything imports the app**, so a module that reads
`APP_ENV` at import reads `development`.

**A step that uses more than `HELD_SECONDS` of CPU on the reference core, on an event loop, is an
error at teardown of its test.** A garbage collection during the step does not count.
`tests/event_loop.py` names the reference core. At import, this module times a fixed workload on
this machine, once per process, and scales the limit to it. So a step that passes here passes on
the reference core. asyncio runs in debug mode on every event
loop here: the one `TestClient` serves the app on, and the one pytest-asyncio runs a test on.
Any other record asyncio logs from WARNING up is an error too, except its line that a step took
long in wall time. A record logged between two tests, by a fixture wider than one test, is an
error at teardown of the next test, or of the last one.

**The app starts only in `tests/started/` and in a tier.** Anywhere else, a test that enters the
lifespan of `create_app` fails and names where it belongs. A route test mounts its router with
`tests.routed.routed` instead. `docs/adr/template/0018`.

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
from typing import Any, Final, NoReturn

import pytest
from fastapi import FastAPI

from app.deployment import (
    DATABASE_URL_ENV,
    DEVELOPMENT,
    ENVIRONMENT_ENV,
    TELEMETRY_ENV,
    TIMEOUTS_ENV,
)

os.environ[ENVIRONMENT_ENV] = DEVELOPMENT
os.environ["PYTHONASYNCIODEBUG"] = "1"

from app import main
from app.identity.shared import SENTINEL_TENANT
from app.migrate import OWNER_URL_ENV
from app.store import Database
from app.store.conn import SCHEMA_ENV
from app.store.memory import MemoryDatabase
from tests.event_loop import (
    Reported,
    StepLimit,
    measure_every_step,
    reported,
    step_limit_on_this_machine,
)
from tests.tiers import python_tiers

TESTS: Final = Path(__file__).parent

STARTED: Final = "started"
"""The folder under `tests/` that holds the tests of the started app, in the default run."""

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


class StartedOutsideItsFolder(RuntimeError):
    """A test outside `tests/started/` and the tiers entered the lifespan of `create_app`."""


@pytest.fixture(autouse=True)
def the_app_starts_only_where_it_may(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside `tests/started/` and the folder of each tier, the lifespan of `create_app` raises
    `StartedOutsideItsFolder` on entry. Building the app is not starting it."""
    folder = request.node.path.relative_to(TESTS).parts[0]
    if folder in {STARTED, *(tier.folder for tier in python_tiers())}:
        return
    test = request.node.nodeid

    def refused(_app: FastAPI) -> NoReturn:
        raise StartedOutsideItsFolder(
            f"{test} started the app, and the app starts only in tests/{STARTED}/ and in a tier. "
            f"Mount the router under test with tests.routed.routed, or move the test to "
            f"tests/{STARTED}/. docs/adr/template/0018 says which test goes where."
        )

    monkeypatch.setattr(main, "lifespan", refused)


@pytest.fixture
def database() -> Database:
    """A fresh in-memory substrate, seeded as the development loop seeds it.
    `tests/integration/conftest.py` puts a Postgres substrate under the same name."""
    return MemoryDatabase(seed_tenant=SENTINEL_TENANT)
