"""The tiers this project has: what the gate does not run, and what runs each one instead.

The project owns this module: the template writes it once and an update never touches it, so
add a tier here when a test needs something a laptop may not have. `tests/tier.py` says what a
tier is. `docs/adr/template/0017`.
"""

from tests.tier import PYTEST_ROOT, Tier

TIERS = (
    Tier(
        runs="make db-test",
        path=f"{PYTEST_ROOT}/integration",
        holds="test_*.py",
        declares="def test_",
        selected_by="Makefile",
        names="tests/integration",
        needs="a Postgres daemon",
    ),
    Tier(
        runs="make observe-test",
        path=f"{PYTEST_ROOT}/observe",
        holds="test_*.py",
        declares="def test_",
        selected_by="Makefile",
        names="tests/observe",
        needs="Docker, to run the otel-lgtm viewer",
    ),
    Tier(
        runs="make test-e2e",
        path="frontend/e2e",
        holds="*.spec.ts",
        declares="test(",
        selected_by="frontend/playwright.config.ts",
        names='testDir: "./e2e"',
        needs="a browser binary",
        excludes="*.live.spec.ts",
    ),
    Tier(
        runs="make test-e2e-live",
        path="frontend/e2e",
        holds="*.live.spec.ts",
        declares="test(",
        selected_by="frontend/playwright.live.config.ts",
        names='testDir: "./e2e"',
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
