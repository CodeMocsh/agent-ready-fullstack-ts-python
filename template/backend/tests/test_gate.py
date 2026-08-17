import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
HOOK = ROOT / ".githooks" / "pre-commit"

THE_GATE = ["lint-check", "openapi-check", "test"]

CI_MUST_ALSO_RUN = {
    "the frontend's lint": "pnpm lint:check",
    "the frontend's tests": "pnpm test",
    "the frontend's half of the contract artifacts": "pnpm openapi:types",
    "the backend's lint": "devtools/lint.py --check",
    "the backend's tests": "uv run pytest",
    "the backend's half of the contract artifacts": "devtools/export_openapi.py",
    "the contract suite against the real backend": "make test-contract",
}

OPT_IN_TIER = ["test-e2e", "test-e2e-live", "db-test"]
"""What needs something fetched or started first: a browser binary, or a database daemon.
The gate runs on every commit, so nothing here may be in it."""


def prerequisites_of(target: str) -> list[str]:
    found = re.search(rf"^{re.escape(target)}:(.*)$", MAKEFILE.read_text(), re.MULTILINE)
    assert found is not None, f"no `{target}:` target in the Makefile"
    return found.group(1).split()


CONDITIONALS = ("ifdef", "ifndef", "ifeq", "ifneq", "else", "endif")
"""Make directives that may sit *inside* a recipe, so reaching one is not the end of it.
`db-test` has an `ifdef` in the middle: with a TEST_DATABASE_URL it uses your database and
with none it starts a container."""


def recipe_of(target: str) -> list[str]:
    lines = MAKEFILE.read_text().splitlines()
    start = next((n for n, line in enumerate(lines) if line.startswith(f"{target}:")), None)
    if start is None:
        return []
    recipe: list[str] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if line.startswith("\t"):
            recipe.append(stripped)
        elif stripped == "" or line.startswith("#") or stripped.startswith(CONDITIONALS):
            continue
        else:
            break
    return recipe


def runs_something(target: str) -> bool:
    return bool(recipe_of(target)) or any(runs_something(p) for p in prerequisites_of(target))


def test_the_gate_is_the_named_list():
    assert prerequisites_of("pre-commit") == THE_GATE


def test_every_member_of_the_gate_actually_runs_a_command():
    for target in THE_GATE:
        assert runs_something(target), f"`{target}` reaches no recipe, so the gate is a no-op"


def test_ci_runs_everything_the_gate_runs():
    workflow = WORKFLOW.read_text()
    for description, command in CI_MUST_ALSO_RUN.items():
        assert command in workflow, (
            f"`make pre-commit` covers {description} and the CI workflow does not run "
            f"{command!r}. Add it to .github/workflows/ci.yml, or take it out of the gate."
        )


def test_the_opt_in_tier_stays_out_of_the_gate():
    reached = set(THE_GATE)
    for target in THE_GATE:
        reached.update(prerequisites_of(target))
    for target in OPT_IN_TIER:
        assert target not in reached, (
            f"`{target}` belongs to the opt-in tier -- it needs a browser binary or a database "
            f"daemon -- and a gate that fetches either is a gate people commit around"
        )
    for target in OPT_IN_TIER:
        assert recipe_of(target), f"`{target}` is documented as the opt-in tier and does nothing"


def test_the_hook_runs_the_gate():
    assert "make -s pre-commit" in HOOK.read_text()


def test_the_hook_says_so_when_it_could_not_run_the_whole_gate():
    assert "PARTIAL RUN" in HOOK.read_text()


OPT_OUT = re.compile(r"--no-verify|\$\{?(?:SKIP|NO_?VERIFY|DISABLE|BYPASS|CI)\b")


def test_the_hook_offers_no_way_to_switch_itself_off():
    found = OPT_OUT.search(HOOK.read_text())
    assert found is None, (
        f"the hook reads {found.group(0)!r}, and a gate with a documented way past it "
        f"is a suggestion. A check that is not worth running every time belongs "
        f"outside the gate, not behind a variable."
    )


def test_make_install_arms_the_hook():
    assert any("hooks" in command for command in recipe_of("install"))
