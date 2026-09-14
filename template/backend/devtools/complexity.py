from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import tomllib
from pathlib import Path

DEFAULT_TOLERANCE = 0.05
DEFAULT_CEILING = 3.0
DEFAULT_MIN_CALLABLES = 50

_DESCRIPTION = "Complexity drift and ceiling checks, measured by ruff. See AGENTS.md."

_COMPLEXITY_IN_RUFF_C901_MESSAGE = re.compile(r"\((\d+) > 0\)")


def measure(ruff: str, paths: list[str]) -> list[int]:
    result = subprocess.run(
        [
            ruff,
            "check",
            "--select",
            "C90",
            "--config",
            "lint.mccabe.max-complexity = 0",
            "--output-format",
            "json",
            *paths,
        ],
        capture_output=True,
        text=True,
    )
    try:
        diagnostics = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        sys.exit(f"error: could not parse ruff output. stderr: {result.stderr.strip()[:200]}")

    found = [
        int(match.group(1))
        for diagnostic in diagnostics
        if (match := _COMPLEXITY_IN_RUFF_C901_MESSAGE.search(str(diagnostic.get("message", ""))))
    ]
    if not found:
        sys.exit(
            "error: ruff reported no callables. Either the paths hold no functions, "
            "or ruff's C901 message format changed and this parser needs updating."
        )
    return sorted(found)


def summarise(complexities: list[int]) -> dict[str, float]:
    index = min(int(len(complexities) * 0.9), len(complexities) - 1)
    return {
        "callables": float(len(complexities)),
        "sum": float(sum(complexities)),
        "mean": round(statistics.mean(complexities), 4),
        "p90": float(complexities[index]),
    }


def load_config(start: Path) -> dict[str, float]:
    pyproject = start / "pyproject.toml"
    if not pyproject.exists():
        return {}
    with pyproject.open("rb") as handle:
        parsed = tomllib.load(handle)
    section = parsed.get("tool", {}).get("complexity", {})
    return {str(key): float(value) for key, value in section.items()}


def check_ceiling(mean: float, ceiling: float) -> int:
    if mean <= ceiling:
        return 0
    print(
        f"\nFAIL: mean CC {mean:.3f} is above the ceiling of {ceiling:.3f}.\n"
        "Accepted drift has accumulated past the absolute limit; this needs\n"
        "refactoring rather than another baseline update.",
        file=sys.stderr,
    )
    return 1


def write_baseline(baseline: Path, now: dict[str, float]) -> None:
    baseline.write_text(json.dumps(now, indent=2) + "\n")


def _load_baseline(baseline: Path) -> dict[str, float]:
    """Read the baseline, recovering a complexity sum that a file written before v0.6.1 omits."""
    recorded = json.loads(baseline.read_text())
    if "sum" in recorded:
        return recorded
    recovered = float(round(float(recorded["mean"]) * float(recorded["callables"])))
    print(f"baseline carries no complexity sum; recovered {recovered:.0f} from mean x callables")
    return {**recorded, "sum": recovered}


def _complexity_moved(drift: float, now: dict[str, float], was: dict[str, float]) -> bool:
    return now["sum"] > was["sum"] if drift > 0 else now["sum"] < was["sum"]


def _report_missing(baseline: Path) -> int:
    print(
        f"\nFAIL: no baseline at {baseline}, and this codebase is now large enough\n"
        "for the drift check to mean something. Record the current level:\n"
        "  uv run python devtools/complexity.py app --baseline .complexity-baseline.json "
        "--update-baseline\n",
        file=sys.stderr,
    )
    return 1


def _report_rise(was: dict[str, float], now: dict[str, float], drift: float, tol: float) -> int:
    print(
        f"\nFAIL: mean CC drifted {was['mean']:.3f} -> {now['mean']:.3f} "
        f"(+{drift:.3f}, tolerance {tol:.3f}).\n"
        "Functions are fattening below the per-function gate; split the ones\n"
        "that grew. If the rise is genuinely warranted, record it with\n"
        "--update-baseline — that lands in the diff for review, so it is not\n"
        "the way to make a build green.",
        file=sys.stderr,
    )
    return 1


def _report_slack(was: dict[str, float], now: dict[str, float], drift: float, tol: float) -> int:
    print(
        f"\nFAIL: baseline is {-drift:.3f} above the tree "
        f"({was['mean']:.3f} -> {now['mean']:.3f}, tolerance {tol:.3f}).\n"
        "A baseline left this far above the tree stops gating: its slack is\n"
        "added to whatever the next commit may spend, so a single change\n"
        f"could raise the mean by {tol - drift:.3f} and still pass.\n"
        "Nobody needs to approve the code getting better, but it does have\n"
        "to be recorded: run `make lint`.",
        file=sys.stderr,
    )
    return 1


def _report_arithmetic(
    was: dict[str, float], now: dict[str, float], drift: float, tol: float
) -> int:
    print(
        f"\nFAIL: mean CC drifted {was['mean']:.3f} -> {now['mean']:.3f} "
        f"({drift:+.3f}, tolerance {tol:.3f}), while the complexity sum went "
        f"{was['sum']:.0f} -> {now['sum']:.0f}.\n"
        "The complexity sum did not move with the mean, so nobody has to consent to\n"
        "this — but it does have to be recorded: run `make lint`.",
        file=sys.stderr,
    )
    return 1


def _tighten(baseline: Path, was: dict[str, float], now: dict[str, float], drift: float) -> int:
    write_baseline(baseline, now)
    print(
        f"baseline tightened: mean CC {was['mean']:.3f} -> {now['mean']:.3f} ({drift:+.3f}); "
        f"commit {baseline}"
    )
    return 0


def _rerecord(baseline: Path, was: dict[str, float], now: dict[str, float], drift: float) -> int:
    write_baseline(baseline, now)
    print(
        f"baseline re-recorded: mean CC {was['mean']:.3f} -> {now['mean']:.3f} ({drift:+.3f}), "
        f"complexity sum {was['sum']:.0f} -> {now['sum']:.0f}.\n"
        "The complexity sum did not move with the mean, so this records the tree's "
        f"shape rather than consent to more complexity. Commit {baseline}"
    )
    return 0


def check_drift(now: dict[str, float], baseline: Path, tolerance: float, tighten: bool) -> int:
    if not baseline.exists():
        return _report_missing(baseline)

    was = _load_baseline(baseline)
    drift = now["mean"] - was["mean"]
    if abs(drift) <= tolerance:
        print(f"drift ok: mean CC {was['mean']:.3f} -> {now['mean']:.3f} ({drift:+.3f})")
        return 0

    if not _complexity_moved(drift, now, was):
        if not tighten:
            return _report_arithmetic(was, now, drift, tolerance)
        return _rerecord(baseline, was, now, drift)

    if drift > tolerance:
        return _report_rise(was, now, drift, tolerance)
    if not tighten:
        return _report_slack(was, now, drift, tolerance)
    return _tighten(baseline, was, now, drift)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=_DESCRIPTION)
    parser.add_argument("paths", nargs="+", help="files or directories to measure")
    parser.add_argument("--ruff", default="ruff", help="ruff executable")
    parser.add_argument("--baseline", type=Path, default=None, help="baseline file to compare")
    parser.add_argument("--update-baseline", action="store_true", help="record the current level")
    parser.add_argument(
        "--tighten-baseline",
        action="store_true",
        help="record a baseline the tree moved away from, instead of failing",
    )
    parser.add_argument("--tolerance", type=float, default=None, help="allowed rise in mean CC")
    parser.add_argument("--ceiling", type=float, default=None, help="mean CC may never exceed this")
    parser.add_argument("--min-callables", type=int, default=None, help="skip below this many")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    config = load_config(Path.cwd())

    def setting(flag: float | int | None, key: str, fallback: float) -> float:
        return float(flag) if flag is not None else config.get(key, fallback)

    tolerance = setting(args.tolerance, "tolerance", DEFAULT_TOLERANCE)
    ceiling = setting(args.ceiling, "ceiling", DEFAULT_CEILING)
    minimum = setting(args.min_callables, "min-callables", DEFAULT_MIN_CALLABLES)

    now = summarise(measure(args.ruff, args.paths))
    print(
        f"callables {now['callables']:.0f}   complexity sum {now['sum']:.0f}   "
        f"mean CC {now['mean']:.3f}   p90 {now['p90']:.0f}"
    )

    if args.baseline and args.update_baseline:
        write_baseline(args.baseline, now)
        print(f"baseline written -> {args.baseline}")
        return 0

    if now["callables"] < minimum:
        print(
            f"under {minimum:.0f} callables: mean is too twitchy to gate on, "
            "so the per-function gate (ruff C901) carries this alone."
        )
        return 0

    status = check_ceiling(now["mean"], ceiling)
    if args.baseline:
        status |= check_drift(now, args.baseline, tolerance, args.tighten_baseline)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
