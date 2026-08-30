"""The one rule that makes `.schema-baseline.json` a gate rather than a mirror.

`make schema` regenerates it. Writing the current hashes wholesale would re-record an edited
body on the way past, and `test_no_shipped_entry_body_has_changed` would never fire again. Why
the baseline exists is in `docs/adr/0003-the-application-never-applies-ddl.md`.
"""

import json
import sys
from pathlib import Path

import pytest

from app.store.conn import DEFAULT_SCHEMA
from app.store.migrate import entry_hashes
from devtools import schema

AS_SHIPPED = "the hash it was recorded with"


def test_a_key_already_recorded_keeps_the_hash_it_was_recorded_with() -> None:
    current = {"0010_tasks": "the hash after somebody edited it", "0011_idx": "new entry"}

    assert schema.merged_baseline({"0010_tasks": AS_SHIPPED}, current) == {
        "0010_tasks": AS_SHIPPED,
        "0011_idx": "new entry",
    }


def test_an_entry_that_no_longer_exists_leaves_the_baseline() -> None:
    previous = {"0010_tasks": "kept", "0090_deleted": "gone"}

    assert schema.merged_baseline(previous, {"0010_tasks": "kept"}) == {"0010_tasks": "kept"}


def test_make_schema_does_not_re_record_a_body_that_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The property the whole gate rests on, driven through `main` rather than around it.

    `merged_baseline` holding on its own proves nothing if `main` does not call it. Regenerating
    would then quiet the gate, and the edit it exists to refuse would ship.
    """
    baseline = tmp_path / ".schema-baseline.json"
    baseline.write_text(json.dumps({"0001_schema": AS_SHIPPED}))
    monkeypatch.setattr(schema, "SCHEMA_BASELINE", baseline)
    monkeypatch.setattr(sys, "argv", ["schema.py", str(tmp_path / "schema.sql")])

    schema.main()

    recorded = json.loads(baseline.read_text())
    assert recorded["0001_schema"] == AS_SHIPPED, "make schema re-recorded a changed body"
    assert set(recorded) == set(entry_hashes(DEFAULT_SCHEMA)), "every other key is recorded"


def test_make_schema_refuses_to_run_without_a_baseline_to_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The baseline ships committed, so a missing one is a deleted one. Treating it as empty
    would re-record every entry and leave a gate that reports success because it stopped
    looking."""
    monkeypatch.setattr(schema, "SCHEMA_BASELINE", tmp_path / "absent.json")
    monkeypatch.setattr(sys, "argv", ["schema.py", str(tmp_path / "schema.sql")])

    with pytest.raises(FileNotFoundError):
        schema.main()
