import re
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "devtools" / "reference_core.py"


def test_the_script_prints_a_positive_number():
    printed = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, check=True
    ).stdout

    assert re.fullmatch(r"[0-9]+\.[0-9]+\n", printed), printed
    assert float(printed) > 0
