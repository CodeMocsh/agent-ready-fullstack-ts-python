import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Final

TOLERANCE: Final = 0.05
SHOWN: Final = 8

_DESCRIPTION: Final = (
    "Count the files of app and of the installed packages that the backend loads before it serves. "
    "See docs/deployment.md#a-cold-start."
)

_PROBE: Final = """
import os
import sys

from app.deployment import BUNDLE_ENV, TELEMETRY_ENV

for name in TELEMETRY_ENV:
    os.environ.pop(name, None)
os.environ[BUNDLE_ENV] = sys.argv[2]

from app.serve import build_server

build_server()
loaded = {}
for name, module in list(sys.modules.items()):
    path = getattr(module, "__file__", None)
    if isinstance(path, str):
        loaded.setdefault(path, name.partition(".")[0])

import json
from importlib.metadata import packages_distributions

installed = packages_distributions()
counted = {
    path: package for path, package in loaded.items() if package == "app" or package in installed
}

with open(sys.argv[1], "w") as out:
    json.dump(counted, out)
"""


def measure() -> dict[str, int]:
    """The files of `app` and of installed packages that `app.serve.build_server` loads, in a new
    process with the production dependencies only and no telemetry, counted by the top-level
    package of each. Raises `CalledProcessError` when that process fails."""
    with tempfile.TemporaryDirectory() as work:
        bundle = Path(work, "bundle")
        bundle.mkdir()
        (bundle / "index.html").write_text("")
        out = Path(work, "loaded.json")
        subprocess.run(
            [
                *("uv", "run", "--isolated", "--no-dev", "--frozen", "--quiet"),
                *("python", "-c", _PROBE, str(out), str(bundle)),
            ],
            check=True,
        )
        loaded: dict[str, str] = json.loads(out.read_text())
    packages: dict[str, int] = {}
    for package in loaded.values():
        packages[package] = packages.get(package, 0) + 1
    return dict(sorted(packages.items()))


def _largest(counts: dict[str, int], sign: str = "") -> str:
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:SHOWN]
    return ", ".join(f"{package} {count:{sign}d}" for package, count in ranked)


def _record(baseline: Path, now: dict[str, int], verb: str) -> int:
    baseline.write_text(json.dumps({"files": sum(now.values()), "packages": now}, indent=2) + "\n")
    print(f"baseline {verb}: {sum(now.values())} files; commit {baseline}")
    return 0


def check(now: dict[str, int], baseline: Path, tighten: bool) -> int:
    files = sum(now.values())
    if not baseline.exists():
        print(
            f"FAIL: no baseline at {baseline}. Record the current count:\n"
            "  uv run python devtools/startup.py --baseline .startup-baseline.json "
            "--update-baseline",
            file=sys.stderr,
        )
        return 1
    was = json.loads(baseline.read_text())
    recorded: int = was["files"]
    grown = {
        package: count - was["packages"].get(package, 0)
        for package, count in now.items()
        if count > was["packages"].get(package, 0)
    }
    if files > recorded * (1 + TOLERANCE):
        print(
            f"FAIL: the startup count is {files} files. The baseline is {recorded}, and the "
            f"tolerance is {TOLERANCE:.0%}.\n"
            f"  grew: {_largest(grown, '+')}\n"
            "Import the package where it is used, or record the rise with --update-baseline. "
            "The rise is then in the diff for review.",
            file=sys.stderr,
        )
        return 1
    if files < recorded * (1 - TOLERANCE):
        if tighten:
            return _record(baseline, now, "tightened")
        print(
            f"FAIL: the startup count is {files} files, and the baseline is {recorded}. "
            "A baseline this far above the count stops gating. Run `make lint`.",
            file=sys.stderr,
        )
        return 1
    print(f"startup ok: {files} files, baseline {recorded}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=_DESCRIPTION)
    parser.add_argument("--baseline", type=Path, required=True, help="baseline file to compare")
    parser.add_argument("--update-baseline", action="store_true", help="record the current count")
    parser.add_argument(
        "--tighten-baseline",
        action="store_true",
        help="record a count that fell past the tolerance, instead of failing",
    )
    args = parser.parse_args()
    now = measure()
    print(f"startup: {sum(now.values())} files; largest: {_largest(now)}", flush=True)
    if args.update_baseline:
        return _record(args.baseline, now, "written")
    return check(now, args.baseline, args.tighten_baseline)


if __name__ == "__main__":
    raise SystemExit(main())
