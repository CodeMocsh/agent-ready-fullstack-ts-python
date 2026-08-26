"""The repository's own agreements, which no other test covers because they are not code.

Everything here reads a file rather than an import: the `Makefile`, the git hook,
`pyproject.toml`, the runners' configs. They have to agree about what gets checked and when,
and nothing but this file notices when they stop -- a gate that lost a member, a tier nothing
runs, a test that switched itself off.

**The hook and the workflow run the same target, and that is the point.** The hook checks a
commit on the machine making it; the workflow checks a push against a fresh checkout nobody
configured, which is what catches a clone where `make hooks` was never run. Neither may grow
its own list of steps -- `test_a_workflow_runs_the_gate_rather_than_a_copy_of_it` is what
insists the workflow names the target instead.

Two of its helpers are imported by `devtools/check_template.sh` in the generator repository,
which is why this module imports no third-party package and does its one version-dependent
import inside the test that needs it.
"""

import re
from pathlib import Path

from tests.tiers import TIERS, python_tiers

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
WORKFLOWS = ROOT / ".github" / "workflows"
HOOK = ROOT / ".githooks" / "pre-commit"
PYPROJECT = ROOT / "backend" / "pyproject.toml"
E2E = ROOT / "frontend" / "e2e"
BACKEND_TESTS = ROOT / "backend" / "tests"
FRONTEND_TESTS = ROOT / "frontend" / "tests"
FRONTEND_SRC = ROOT / "frontend" / "src"

THE_GATE = ["lint-check", "openapi-check", "test"]

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


def without_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))


def workflows() -> list[tuple[Path, str]]:
    """Every workflow this project ships, with its comment lines stripped. A workflow that
    named the gate in a comment and ran `true` would satisfy a plain substring check."""
    found = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    return [(path, without_comments(path.read_text())) for path in found]


def test_a_workflow_runs_the_gate_rather_than_a_copy_of_it():
    """A workflow that runs its own list of steps drifts from `make pre-commit` silently, in
    the direction of checking less, and the drift shows up as a green push that a commit would
    have refused. Run the target instead. If it needs to run only part of the gate, make that
    part a target too.

    The body is read with comments stripped, so a workflow naming the target in a comment and
    running `true` does not satisfy this."""
    shipped = workflows()

    assert shipped, (
        "no workflow ships, so this test loops over nothing and passes without checking "
        "anything -- the shape a check takes when it has quietly stopped being one. Restore "
        "`.github/workflows/ci.yml`, or, if this project runs its checks somewhere GitHub "
        "cannot see, delete this assertion on purpose rather than leaving it green."
    )

    for path, body in shipped:
        assert "make pre-commit" in body or "check_template.sh" in body, (
            f"{path.relative_to(ROOT)} does not run `make pre-commit`. A workflow that "
            f"re-lists the gate's steps is a second copy of the gate, and the copy is what "
            f"goes stale -- point it at the target, or add a target for the part it runs."
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
            f"nothing -- and the gate would not notice, because the gate does not run it"
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
