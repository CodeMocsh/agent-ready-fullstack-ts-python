"""The limit on this machine is `HELD_SECONDS` scaled by how fast it runs a fixed workload."""

import subprocess
import sys
from pathlib import Path

import pytest

from devtools.reference_core import REFERENCE_WORKLOAD_SECONDS
from tests.event_loop import (
    HELD_SECONDS,
    StepLimit,
)


def test_the_limit_on_the_reference_core_is_held_seconds() -> None:
    assert StepLimit(REFERENCE_WORKLOAD_SECONDS).seconds == HELD_SECONDS


def test_the_limit_on_a_faster_machine_comes_to_held_seconds_on_the_reference_core() -> None:
    faster = StepLimit(REFERENCE_WORKLOAD_SECONDS / 4)

    assert faster.seconds == pytest.approx(HELD_SECONDS / 4)
    assert faster.on_the_reference_core(faster.seconds) == pytest.approx(HELD_SECONDS)


COLLECTION_DURING_A_FIRST_READ = """
import gc
import sys
import threading

from tests.event_loop import StepLimit, _cpu_seconds_without_collections, measure_every_step

measure_every_step(StepLimit(0.05))
raised = []
sys.unraisablehook = lambda unraisable: raised.append(repr(unraisable.exc_value))


def read_after_a_collection():
    gc.set_threshold(1)
    allocated = [[number] for number in range(3)]
    _cpu_seconds_without_collections()
    gc.set_threshold(700)
    return allocated


threads = [threading.Thread(target=read_after_a_collection) for _ in range(20)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()
print(raised)
"""


def test_a_collection_during_a_thread_s_first_read_of_its_timings_raises_nothing() -> None:
    ran = subprocess.run(
        [sys.executable, "-c", COLLECTION_DURING_A_FIRST_READ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "[]"
