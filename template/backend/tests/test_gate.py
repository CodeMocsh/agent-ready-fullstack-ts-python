"""The repository's own agreements, which no other test covers because they are not code.

Everything here reads a file rather than an import: the `Makefile`, the git hook, the CI
workflow, `pyproject.toml`, the runners' configs. They have to agree about what gets checked
and when, and nothing but this file notices when they stop -- a gate that lost a member, a
tier nothing runs, a test that switched itself off.

Two of its helpers are imported by `devtools/check_template.sh` in the generator repository,
which is why this module imports no third-party package and does its one version-dependent
import inside the test that needs it.
"""

import re
from pathlib import Path

from tests.tiers import TIERS, python_tiers

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
HOOK = ROOT / ".githooks" / "pre-commit"
PYPROJECT = ROOT / "backend" / "pyproject.toml"
E2E = ROOT / "frontend" / "e2e"
BACKEND_TESTS = ROOT / "backend" / "tests"
FRONTEND_TESTS = ROOT / "frontend" / "tests"
FRONTEND_SRC = ROOT / "frontend" / "src"

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

OPT_IN_TIER = [tier.target for tier in TIERS]
"""Read from `tiers.py` rather than listed again here. Every one needs something fetched or
started first, the gate runs on every commit, so nothing here may be in it."""


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


def test_every_tier_still_holds_tests():
    """Each tier is selected by a path rather than by a list of filenames, so a test added to
    one is picked up rather than forgotten. What a path can do instead is stop matching -- an
    emptied folder, a renamed suffix -- and then the runner reports "nothing to run", which
    reads as a broken target rather than as a tier that stopped existing. Both runners have
    the failure: `pytest` exits 5, and Playwright says "No tests found"."""
    for tier in TIERS:
        assert tier.declaring_files(ROOT), (
            f"no {tier.holds} under {tier.path} declares a test, so `{tier.runs}` selects "
            f"nothing -- and neither the gate nor CI would notice, because neither runs it"
        )


def test_every_tier_is_selected_by_a_file_that_still_names_it():
    """The Makefile points `db-test` at its folder; a Playwright config points each e2e target
    at its own. Rename the folder and the command still exists, still exits 0 on some other
    day's argument, and runs none of these tests."""
    for tier in TIERS:
        selects = (ROOT / tier.selected_by).read_text()
        assert tier.path.rsplit("/", 1)[-1] in selects, (
            f"{tier.selected_by} no longer names {tier.path}, so `{tier.runs}` does not "
            f"select the tier it is supposed to run"
        )
        assert not tier.excludes or tier.excludes in selects, (
            f"{tier.selected_by} no longer ignores {tier.excludes}, so `{tier.runs}` runs "
            f"another tier's tests as well as its own"
        )


def test_every_python_tier_is_out_of_the_default_run():
    """A tier that `norecursedirs` does not name is collected by a plain `pytest`, where it
    needs the daemon the gate refuses to require. That is the pressure a skip comes from, so
    the declaration and the setting have to agree. The frontend's tiers need no such setting:
    their runner is a different program, pointed at its own folder."""
    import tomllib

    settings = tomllib.loads(PYPROJECT.read_text())["tool"]["pytest"]["ini_options"]
    declared = sorted({tier.folder for tier in python_tiers()})
    assert sorted(settings["norecursedirs"]) == declared, (
        f"norecursedirs is {settings['norecursedirs']} and the Python tiers are {declared}. "
        f"A tier missing from the setting runs in the gate; a folder named there and not in "
        f"tiers.py is a folder nothing runs at all."
    )


SKIP, XFAIL = "skip", "xfail"
"""Assembled from these rather than written out, so this file can be scanned like any other.
Spelled literally, every Python marker below would match here first and the scan would have
to exempt the one file whose job is to run it."""

SKIP_MARKERS = {
    ".py": (
        f"pytest.{SKIP}(",
        f"pytest.{XFAIL}(",
        f"pytest.mark.{SKIP}",
        f"pytest.mark.{XFAIL}",
        f"importor{SKIP}(",
    ),
    ".ts": (".skip", ".only", ".fixme", ".todo", ".runIf", "skip:", "only: true"),
}
SKIP_MARKERS[".tsx"] = SKIP_MARKERS[".ts"]
"""Every way a test in this project could decide not to run itself, or decide that nothing
else runs. Each half offers more than one spelling, and a list holding only the obvious one
is a gate with a door in it: pytest has the marker and the imperative call, and `skipif` and
`importorskip` on top; vitest has `.skipIf`, `.runIf` and an options object; playwright has
`.fixme`. Matching without the parenthesis is what covers `.skipIf` and `.skip.each` in the
same entry. `.only` belongs here with the skips -- it silently drops every other test in the
file, which is the same failure with a smaller blast radius."""


def files_holding_tests() -> list[Path]:
    """Every test file in both halves, this one included. `src/` is here for the day someone
    puts a spec beside the component it covers -- by suffix, so ordinary source is not read
    against markers that mean something else in application code."""
    roots = [
        (BACKEND_TESTS, "*.py"),
        (FRONTEND_TESTS, "*.ts*"),
        (E2E, "*.ts*"),
        (FRONTEND_SRC, "*.test.ts*"),
        (FRONTEND_SRC, "*.spec.ts*"),
    ]
    found = [path for root, pattern in roots for path in root.rglob(pattern)]
    return [path for path in found if path.suffix in SKIP_MARKERS]


def switched_off() -> list[tuple[Path, str]]:
    """Every test file that decides for itself whether to run, and the marker that says so.

    `devtools/check_template.sh` in the generator repository calls this, so the rule holds in
    a run where no test executes at all. A second copy of the scan in shell would be a copy
    that drifts, and the half that drifts is the half nobody notices.
    """
    return [
        (path, marker)
        for path in files_holding_tests()
        for marker in SKIP_MARKERS[path.suffix]
        if marker in path.read_text(encoding="utf-8")
    ]


def test_no_test_switches_itself_off():
    """A test that needs something a laptop may not have belongs in a tier that is run on
    demand -- `tests/integration/` is the one this project ships -- and never behind a skip.
    A skipped test exits 0 and reads exactly like a test that passed, so a run where every
    one of them skipped is indistinguishable from a run where every one of them ran."""
    found = switched_off()
    named = ", ".join(f"{path.relative_to(ROOT)} uses {marker!r}" for path, marker in found)

    assert not found, (
        f"{named}. Tests here do not skip: move it into a tier "
        f"({', '.join(tier.path for tier in TIERS)}, or one of its own) and run that tier, "
        f"so what did not run is a folder nobody selected rather than a green result that "
        f"checked nothing."
    )


VARIABLE = re.compile(r"\$\((\w+)\)")


def expanded(line: str) -> str:
    """`$(NAME)` replaced by what the Makefile assigns it, so a recipe that reaches its tier
    through a variable reads the same as one that spells the path out."""
    makefile = MAKEFILE.read_text()

    def assigned(found: re.Match[str]) -> str:
        value = re.search(rf"^{found.group(1)} *[:?]?= *(.*)$", makefile, re.MULTILINE)
        return value.group(1).strip() if value else found.group(0)

    return VARIABLE.sub(assigned, line)


def test_the_python_tier_is_selected_whole_and_not_by_a_list():
    """`make db-test` names the directory rather than the files in it, so a test added to
    `tests/integration/` runs without anyone remembering to list it. This is what notices if
    that ever becomes a list of filenames again -- the arrangement it replaced, where a
    Postgres test in a new file ran in neither the tier nor the gate."""
    for tier in python_tiers():
        assert (BACKEND_TESTS / tier.folder).is_dir(), f"`{tier.runs}` selects a missing folder"
        selects = [expanded(line) for line in recipe_of(tier.target) if "pytest" in line]
        assert selects, f"`{tier.runs}` runs no pytest"
        for line in selects:
            assert line.endswith(f"tests/{tier.folder}"), (
                f"`{tier.runs}` runs `{line}`, which does not select {tier.folder} whole. A "
                f"list of filenames is the shape this refuses: it drifts, and nothing notices."
            )


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
