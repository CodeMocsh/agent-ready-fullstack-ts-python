"""The tiers: what the gate does not run, and what runs each one instead.

A tier is a set of tests that needs something a laptop may not have -- a daemon, a browser, a
login, a model. It is not in `make pre-commit`, because a gate that fetches a browser is a
gate people learn to commit around, and it is never a reason to write a skip: a skipped test
exits 0 and reads exactly like a test that passed, while a tier that was not selected is
reported as not run. `docs/adr/0005` holds that reasoning and the options it rejected.

**Each one is named for what it needs, not for who starts it.** `tests/integration` needs a
server; `e2e` needs a browser. Who types the command is the least stable thing about a
tier -- `devtools/check_template.sh` already runs `make db-test` unattended wherever Docker
answers, and any of these becomes a nightly workflow the day someone writes one -- so a name
like `manual` would be false by the time anybody read it. It is also taken: in testing
vocabulary a manual test is one a person performs by hand, and every test here is automated.

One declaration, four readers: `conftest.py` prints what a run left out, `test_gate.py`
checks that the folder still holds tests and that nothing here creeps into the gate,
`norecursedirs` keeps the Python tiers out of the default run, and `selected_by` is the file
that points each command at its folder. A tier missing from any of them is a tier that
quietly stops being run, which is the failure this file exists to prevent.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

PYTEST_ROOT = "backend/tests"
"""Where pytest looks. A tier under here is one `norecursedirs` has to name."""


@dataclass(frozen=True)
class Tier:
    runs: str
    """The command. Its `make` target must stay out of the gate."""

    path: str
    """What that command selects, from the repository root."""

    holds: str
    """The files inside it the runner collects."""

    declares: str
    """What makes one of those files hold a test. A glob is satisfied by an empty file."""

    selected_by: str
    """The file that points `runs` at `path`: a Makefile recipe, or a runner's config."""

    needs: str
    """Why it is not in the gate. This is the sentence the tier is named after."""

    excludes: str = ""
    """What its runner ignores inside `path`, spelled as its config spells it.

    Two tiers share `frontend/e2e`, and without this the mock one is satisfied by a live spec
    it never runs -- so every mock spec could stop declaring a test and nothing would say so.
    """

    @property
    def target(self) -> str:
        """The `make` target, for the checks that read the Makefile."""
        return self.runs.removeprefix("make ")

    @property
    def folder(self) -> str:
        """Its name under `tests/`, which is what `norecursedirs` carries. Empty for a tier
        pytest never looks at."""
        return self.path.removeprefix(f"{PYTEST_ROOT}/") if self.run_by_pytest else ""

    @property
    def run_by_pytest(self) -> bool:
        return self.path.startswith(f"{PYTEST_ROOT}/")

    def declaring_files(self, root: Path) -> list[Path]:
        """The files under it that actually declare a test, not merely the ones named like
        one. Existence is the weaker check and an empty file passes it: the glob finds
        `test_x.py`, the runner collects nothing, and the tier is gone while the gate reports
        green. Recursive, because both runners are."""
        found = self.selects(root / self.path)
        return [one for one in found if self.declares in one.read_text(encoding="utf-8")]

    def selects(self, where: Path) -> Iterator[Path]:
        """What the runner would collect from `where`, its own exclusion applied."""
        return (one for one in where.rglob(self.holds) if not self.ignores(one))

    def ignores(self, path: Path) -> bool:
        return bool(self.excludes) and path.match(self.excludes)


TIERS = (
    Tier(
        runs="make db-test",
        path=f"{PYTEST_ROOT}/integration",
        holds="test_*.py",
        declares="def test_",
        selected_by="Makefile",
        needs="a Postgres daemon",
    ),
    Tier(
        runs="make test-e2e",
        path="frontend/e2e",
        holds="*.spec.ts",
        declares="test(",
        selected_by="frontend/playwright.config.ts",
        needs="a browser binary",
        excludes="*.live.spec.ts",
    ),
    Tier(
        runs="make test-e2e-live",
        path="frontend/e2e",
        holds="*.live.spec.ts",
        declares="test(",
        selected_by="frontend/playwright.live.config.ts",
        needs="a browser binary, and `make dev` in another terminal",
    ),
)


def python_tiers() -> list[Tier]:
    """The tiers pytest would collect if `norecursedirs` did not name them.

    A list rather than a dict keyed by folder: two tiers may share one folder and differ by
    what they select, the way the e2e pair does, and a dict would silently keep one of them.
    The frontend's tiers are out of the default run because their runner is a different
    program entirely, so only these have a setting to keep in step.
    """
    return [tier for tier in TIERS if tier.run_by_pytest]
