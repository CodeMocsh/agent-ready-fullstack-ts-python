from pathlib import Path

from tests.test_gate import a_clone_with_a_gate, run_the_gate


def test_a_gate_within_its_budget_on_the_clock_passes_and_says_so(tmp_path: Path):
    clone = a_clone_with_a_gate(tmp_path, "echo ran >> ran.log", budget=5)

    ran = run_the_gate(clone)

    assert ran.returncode == 0, ran.stderr
    assert "s on the clock of a 5s budget" in ran.stderr


def test_a_gate_that_waits_past_its_budget_on_the_clock_is_over_it(tmp_path: Path):
    clone = a_clone_with_a_gate(tmp_path, "sleep 2; echo ran >> ran.log", budget=1)

    ran = run_the_gate(clone)

    assert ran.returncode != 0
    assert "Over budget" in ran.stderr
