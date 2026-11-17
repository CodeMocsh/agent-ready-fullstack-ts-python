"""The repository's own agreements, which no other test covers because they are not code.

Everything here reads a file rather than an import: the `Makefile`, the git hook,
`pyproject.toml`, the runners' configs. They have to agree about what gets checked and when,
and nothing but this file notices when they stop -- a gate that lost a member, a tier nothing
runs, a test that switched itself off.

The hook runs `make gate-commit`. CI runs `make suite`, which includes `make gate`.
Each caller names a target instead of copying its steps.

Two of its helpers are imported by `devtools/check_template.sh` in the generator repository,
which is why this module imports no third-party package at the top and does each such import
inside the test that needs it.
"""

import os
import re
import subprocess
from pathlib import Path

from tests.tier import Tier
from tests.tiers import TIERS, python_tiers

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
HOOK = ROOT / ".githooks" / "pre-commit"
RUNNER = ROOT / "devtools" / "gate.sh"
HOLD = ROOT / "devtools" / "hold-the-gate.pl"
WORKTREE_TREE = ROOT / "devtools" / "worktree-tree.sh"
INSTALL = ROOT / "devtools" / "install.sh"
PYPROJECT = ROOT / "backend" / "pyproject.toml"
E2E = ROOT / "frontend" / "e2e"
BACKEND_TESTS = ROOT / "backend" / "tests"
FRONTEND_TESTS = ROOT / "frontend" / "tests"
FRONTEND_SRC = ROOT / "frontend" / "src"

THE_GATE = ["secrets", "lint-check", "openapi-check", "test"]

OPT_IN_TIER = [tier.target for tier in TIERS]
"""Read from `tiers.py` rather than listed again here. Every one needs something fetched or
started first, the gate runs on every commit, so nothing here may be in it."""


def prerequisites_of(target: str) -> list[str]:
    makefile = MAKEFILE.read_text(encoding="utf-8")
    found = re.search(rf"^{re.escape(target)}:(.*)$", makefile, re.MULTILINE)
    assert found is not None, f"no `{target}:` target in the Makefile"
    return found.group(1).split()


CONDITIONALS = ("ifdef", "ifndef", "ifeq", "ifneq", "else", "endif")
"""Make directives that may sit *inside* a recipe, so reaching one is not the end of it.
`db-test` has an `ifdef` in the middle: with a TEST_DATABASE_URL it uses your database and
with none it starts a server."""


def recipe_of(target: str) -> list[str]:
    lines = MAKEFILE.read_text(encoding="utf-8").splitlines()
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


def test_every_tier_is_the_template_s_tier():
    """`tests/tiers.py` is the project's and an update never touches it, so a project generated
    before `tests/tier.py` existed still defines its own `Tier` there, and stops receiving the
    template's changes to it."""
    own = [tier.runs for tier in TIERS if type(tier) is not Tier]

    assert own == [], (
        f"{own} are built on a Tier that tests/tiers.py defines itself. Delete it, and import "
        f"Tier and PYTEST_ROOT from tests.tier instead."
    )


def test_the_gate_is_the_named_list():
    assert prerequisites_of("gate") == THE_GATE


def test_every_member_of_the_gate_actually_runs_a_command():
    for target in THE_GATE:
        assert runs_something(target), f"`{target}` reaches no recipe, so the gate is a no-op"


COMMENT_OPENERS = ("#", "//")
"""Every way a line in one of the files read here begins a comment: `#` in a Makefile, a
workflow and a pyproject, `//` in a Playwright config. A whole-line opener is all that is
needed, because what these checks look for is a path or a target sitting on a line of its
own."""


def without_comments(text: str) -> str:
    """The text a check may believe, with every comment line removed, so a file that only
    mentions what a check looks for in a comment does not satisfy it."""
    return "\n".join(
        line for line in text.splitlines() if not line.strip().startswith(COMMENT_OPENERS)
    )


def test_the_opt_in_tier_stays_out_of_the_gate():
    reached = set(THE_GATE)
    for target in THE_GATE:
        reached.update(prerequisites_of(target))
    for target in OPT_IN_TIER:
        assert target not in reached, (
            f"`{target}` belongs to the opt-in tier -- it needs a browser binary or a database "
            f"daemon -- and a gate that needs either is a gate people commit around"
        )
    for target in OPT_IN_TIER:
        assert recipe_of(target), f"`{target}` is documented as the opt-in tier and does nothing"


def reached_from(target: str) -> set[str]:
    reached = {target}
    for prerequisite in prerequisites_of(target):
        reached |= reached_from(prerequisite)
    return reached


FETCHING = ("playwright install", "devtools/install.sh")
"""What a recipe line runs when it fetches: the browser itself, or the script that fetches it and
everything else `make install` downloads."""


def test_only_install_fetches_the_browser():
    makefile = MAKEFILE.read_text(encoding="utf-8")
    targets = re.findall(r"^([\w-]+):(?!=)", makefile, re.MULTILINE)
    fetching = [t for t in targets if any(f in line for line in recipe_of(t) for f in FETCHING)]
    assert any("devtools/install.sh $(E2E_BROWSERS)" in line for line in recipe_of("install")), (
        "`make install` no longer names the browsers that devtools/install.sh fetches"
    )
    assert 'playwright install "$@"' in INSTALL.read_text(encoding="utf-8"), (
        "devtools/install.sh no longer fetches the browsers it is given"
    )
    assert fetching == ["install"], (
        f"{fetching} run `playwright install` or devtools/install.sh. Only `install` may. A "
        f"target that fetches the browser needs the network every time it runs."
    )
    for target in OPT_IN_TIER:
        assert "install" not in reached_from(target), (
            f"`{target}` reaches `install`, so it fetches the browser and needs the network"
        )
        calls_install = [
            line for line in recipe_of(target) if "$(MAKE)" in line and "install" in line.split()
        ]
        assert calls_install == [], (
            f"`{target}` runs `make install` in its recipe, so it fetches the browser and needs "
            f"the network"
        )


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
    day's argument, and runs none of these tests.

    It matches `names` rather than the folder's own name, because both e2e tiers end in `e2e`
    and every file that selects one says `e2e` somewhere else as well -- in a target, in a
    comment, in a sibling's config. `"e2e" in text` was therefore true of a config pointing at
    another directory entirely, which is the one thing this test exists to catch. Comments go
    for the same reason: every one of these folders is described in prose somewhere above the
    line that selects it."""
    for tier in TIERS:
        selects = without_comments((ROOT / tier.selected_by).read_text(encoding="utf-8"))
        assert tier.names in selects, (
            f"{tier.selected_by} no longer says `{tier.names}`, so `{tier.runs}` does not "
            f"select {tier.path}, the tier it is supposed to run"
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

    settings = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]
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
    through a variable reads the same as one that spells the path out.

    An unassigned name raises rather than passing through as literal text. Make expands one to
    the empty string, so the recipe this reads would run `pytest` with no path at all -- and
    leaving `$(NAME)` in place would fail the caller's `endswith` for a reason that names the
    wrong problem."""
    makefile = MAKEFILE.read_text(encoding="utf-8")

    def assigned(found: re.Match[str]) -> str:
        value = re.search(rf"^{found.group(1)} *[:?]?= *(.*)$", makefile, re.MULTILINE)
        if value is None:
            raise AssertionError(
                f"the Makefile reads {found.group(0)} and assigns it nowhere. Make expands "
                f"that to nothing, so the recipe runs without the path it means to select."
            )
        return value.group(1).strip()

    return VARIABLE.sub(assigned, line)


def test_the_python_tier_is_selected_whole_and_not_by_a_list():
    """`make db-test` names the directory rather than the files in it, so a test added to
    `tests/integration/` runs without anyone remembering to list it. This is what notices if
    that ever becomes a list of filenames again -- the arrangement it replaced, where a
    Postgres test in a new file ran in neither the tier nor the gate."""
    for tier in python_tiers():
        assert (BACKEND_TESTS / tier.folder).is_dir(), f"`{tier.runs}` selects a missing folder"
        selects = [
            expanded(line)
            for line in recipe_of(tier.target)
            if "pytest" in line or "devtools/db-test.sh" in line
        ]
        assert selects, f"`{tier.runs}` runs no pytest"
        for line in selects:
            assert line.endswith(f"tests/{tier.folder}"), (
                f"`{tier.runs}` runs `{line}`, which does not select {tier.folder} whole. A "
                f"list of filenames is the shape this refuses: it drifts, and nothing notices."
            )


def test_the_hook_and_make_pre_commit_run_the_gate_through_one_runner():
    """A gate started by hand that skipped the runner would take no lock and record no pass."""
    assert "make -s pre-commit" in HOOK.read_text(encoding="utf-8")
    assert recipe_of("pre-commit") == ["@sh devtools/gate.sh"]
    assert "make -s gate-commit" in RUNNER.read_text(encoding="utf-8")


def test_the_runner_says_so_when_it_could_not_run_the_whole_gate():
    assert "PARTIAL RUN" in RUNNER.read_text(encoding="utf-8")


OPT_OUT = re.compile(r"--no-verify|\$\{?(?:SKIP|NO_?VERIFY|DISABLE|BYPASS|CI)\b")


def test_the_gate_offers_no_way_to_switch_itself_off():
    for path in (HOOK, RUNNER):
        found = OPT_OUT.search(path.read_text(encoding="utf-8"))
        assert found is None, (
            f"{path.name} reads {found.group(0)!r}, and a gate with a documented way past it "
            f"is a suggestion. A check that is not worth running every time belongs "
            f"outside the gate, not behind a variable."
        )


THE_LOCK = re.compile(r'^lock="/tmp/[a-z0-9-]+-pre-commit-\$\(id -u\)\.lock"$', re.MULTILINE)
THE_BUDGET = re.compile(r"^BUDGET=[0-9]+$", re.MULTILINE)


def a_clone_with_a_gate(tmp_path: Path, recipe: str, budget: int = 600) -> Path:
    """A git checkout with both halves "installed", the runner and its helpers copied in, and a
    `gate-commit` target that runs `recipe`. The lock is moved into `tmp_path`, so a gate already
    holding this project's lock -- the one running this test -- does not hold this one."""
    clone = tmp_path / "clone"
    (clone / "devtools").mkdir(parents=True)
    runner = RUNNER.read_text(encoding="utf-8")
    assert THE_LOCK.search(runner), "the runner no longer takes the lock this test moves"
    assert THE_BUDGET.search(runner), "the runner no longer sets the budget this test moves"
    runner = THE_LOCK.sub(f'lock="{tmp_path}/gate.lock"', runner)
    runner = THE_BUDGET.sub(f"BUDGET={budget}", runner)
    (clone / "devtools" / "gate.sh").write_text(runner, encoding="utf-8")
    for helper in (WORKTREE_TREE, HOLD):
        (clone / "devtools" / helper.name).write_text(helper.read_text(encoding="utf-8"))
    (clone / "frontend" / "node_modules").mkdir(parents=True)
    (clone / "backend" / ".venv").mkdir(parents=True)
    (clone / "Makefile").write_text(f"gate-commit:\n\t@{recipe}\n", encoding="utf-8")
    (clone / ".gitignore").write_text("ran.log\nnode_modules/\n.venv/\n", encoding="utf-8")
    (clone / "kept.txt").write_text("one\n", encoding="utf-8")
    tools = tmp_path / "bin"
    tools.mkdir()
    for tool in ("pnpm", "uv"):
        (tools / tool).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (tools / tool).chmod(0o755)
    subprocess.run(["git", "init", "-q"], cwd=clone, check=True)
    return clone


def run_the_gate(clone: Path) -> subprocess.CompletedProcess[str]:
    tools = clone.parent / "bin"
    return subprocess.run(
        ["sh", "devtools/gate.sh"],
        cwd=clone,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}"},
    )


def times_the_gate_ran(clone: Path) -> int:
    ran = clone / "ran.log"
    return len(ran.read_text().splitlines()) if ran.exists() else 0


def test_make_install_arms_the_hook():
    assert any("hooks" in command for command in recipe_of("install"))
