from pathlib import Path

from tests.test_gate import a_clone_with_a_gate, run_the_gate


def test_a_gate_the_clock_alone_slows_is_within_budget(tmp_path: Path):
    """A gate that only waits on the clock spends no CPU-seconds of its budget."""
    clone = a_clone_with_a_gate(tmp_path, "sleep 2; echo ran >> ran.log", budget=1)

    ran = run_the_gate(clone)

    assert ran.returncode == 0, ran.stderr
    assert "CPU-seconds of a 1 budget" in ran.stderr


def test_a_gate_that_works_past_its_budget_is_over_it(tmp_path: Path):
    clone = a_clone_with_a_gate(
        tmp_path, "perl -e '1 while (times)[0] < 2'; echo ran >> ran.log", budget=1
    )

    ran = run_the_gate(clone)

    assert ran.returncode != 0
    assert "Over budget" in ran.stderr
