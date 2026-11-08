import json
import shlex
import subprocess
import sys
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path


def run(arguments, cwd):
    result = subprocess.run(arguments, cwd=cwd, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@contextmanager
def frontend_probes(frontend):
    sources = {
        "devtools/slow/selection-probe.test.ts": "undefined",
        "devtools/slow/nested/selection-probe.test.ts": "undefined",
        "components/slow/selection-probe.test.tsx": "object",
        "slow/selection-probe.test.ts": "object",
        "devtools/slower/selection-probe.test.ts": "undefined",
    }
    created = []
    paths = []
    try:
        for name, environment in sources.items():
            path = frontend / "tests" / name
            assert not path.exists(), f"probe would replace {path}"
            for parent in reversed(path.parents):
                if not parent.exists():
                    parent.mkdir()
                    created.append(parent)
            path.write_text(
                'import { expect, test } from "vitest";\n'
                'test("selection probe", () => {\n'
                f'  expect(typeof document).toBe("{environment}");\n'
                '});\n'
            )
            paths.append(path)
        yield
    finally:
        for path in paths:
            path.unlink()
        for path in reversed(created):
            path.rmdir()


def frontend_selection(frontend):
    scripts = json.loads((frontend / "package.json").read_text())["scripts"]
    command = shlex.split(scripts["test:commit"])
    assert command[:2] == ["vitest", "run"], command
    full_command = shlex.split(scripts["test"])
    assert full_command[:2] == ["vitest", "run"], full_command
    commit_command = ["pnpm", "exec", "vitest", "list", *command[2:], "--json"]
    with frontend_probes(frontend):
        all_tests = json.loads(
            run(["pnpm", "exec", "vitest", "list", *full_command[2:], "--json"], frontend)
        )
        commit_tests = json.loads(run(commit_command, frontend))
        files = {path.resolve() for path in (frontend / "tests").rglob("*.test.*")}
        selected = {Path(test["file"]).resolve() for test in all_tests}
        assert selected == files, f"frontend selection differs: {selected ^ files}"
        projects = defaultdict(set)
        for test in all_tests:
            projects[test["file"]].add(test["projectName"])
        assert all(len(names) == 1 for names in projects.values()), projects

        def key(test):
            return test["file"], test["name"], test["projectName"]

        expected = {
            key(test)
            for test in all_tests
            if test["projectName"] in {"node", "smoke"}
            and "slow" not in Path(test["file"]).relative_to(frontend / "tests").parts
        }
        actual = {key(test) for test in commit_tests}
        assert expected and actual == expected, f"frontend commit selection differs: {actual ^ expected}"
        run(["pnpm", "exec", *full_command, "selection-probe"], frontend)
        run(["pnpm", "exec", *command, "selection-probe"], frontend)
    print("frontend: every file in one project; slow tests retain their environment and full coverage")


def backend_selection(root):
    backend = root / "backend"
    command = shlex.split(run(["make", "-s", "-n", "test-commit-backend"], root))
    assert command[:3] == ["cd", "backend", "&&"], command
    full_commands = run(["make", "-s", "-n", "test-fast"], root).splitlines()
    full_command = next(shlex.split(line) for line in full_commands if line.startswith("cd backend &&"))
    all_tests = {
        line for line in run([*full_command[3:], "--collect-only"], backend).splitlines()
        if "::" in line
    }
    commit_tests = {
        line for line in run([*command[3:], "--collect-only"], backend).splitlines() if "::" in line
    }
    expected = {
        test for test in all_tests
        if not test.startswith("tests/started/") and "slow" not in Path(test.split("::")[0]).parts
    }
    assert expected and commit_tests == expected, (
        f"backend commit selection differs: {commit_tests ^ expected}"
    )
    sys.path.insert(0, str(backend))
    from tests.tiers import python_tiers

    external = [root / tier.path for tier in python_tiers()]
    files = {
        path.relative_to(backend).as_posix()
        for path in (backend / "tests").rglob("test_*.py")
        if not any(path.is_relative_to(folder) for folder in external)
    }
    selected = {test.split("::")[0] for test in all_tests}
    assert selected == files, f"backend full selection differs: {selected ^ files}"
    print(f"backend: {len(commit_tests)} commit tests; {len(all_tests)} default full-suite tests")


root = Path.cwd().resolve()
frontend_selection(root / "frontend")
backend_selection(root)
