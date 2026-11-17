"""The reference core, and how many of its CPU-seconds one CPU-second on this machine is.

The reference core is one core of a Cloud Build `E2_HIGHCPU_32` machine, in `python:3.12-slim`
on x86_64, Python 3.12.16. Run as a script, this module prints how many CPU-seconds on the
reference core one CPU-second on this machine comes to. Divide a limit on the reference core by
that number to find the limit on this machine.
"""

import statistics
import time
from collections.abc import Callable
from typing import Final

REFERENCE_WORKLOAD_SECONDS: Final = 0.246
"""The CPU time `workload` uses on the reference core. The least of 7 runs."""

WORKLOAD_RUNS: Final = 5
"""How many times `workload_seconds_here` runs `workload`."""


def workload() -> int:
    """Fixed CPU work in pure Python. `REFERENCE_WORKLOAD_SECONDS` is true for this code only."""
    total = 0
    held: dict[str, list[int]] = {}
    for i in range(400_000):
        key = f"k{i % 5000}"
        held.setdefault(key, []).append(i)
        total += len(key) * (i % 7)
    for values in held.values():
        total += sum(sorted(values, reverse=True)[:3])
    return total


def cpu_seconds(work: Callable[[], object]) -> float:
    """The CPU time this thread uses to run `work` once."""
    started = time.thread_time()
    work()
    return time.thread_time() - started


def workload_seconds_here() -> float:
    """Runs `workload` `WORKLOAD_RUNS` times on this thread, now, and returns the median CPU
    time. Blocks for about `WORKLOAD_RUNS` times `REFERENCE_WORKLOAD_SECONDS` on the reference
    core."""
    return statistics.median(cpu_seconds(workload) for _ in range(WORKLOAD_RUNS))


def main() -> None:
    print(f"{REFERENCE_WORKLOAD_SECONDS / workload_seconds_here():.3f}")


if __name__ == "__main__":
    main()
