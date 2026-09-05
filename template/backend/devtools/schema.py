"""Regenerates what `make schema` owns: the SQL script, and the schema baseline.

`.schema-baseline.json` ships committed and `check_template.sh` requires it, so a missing one is
a failure and this reads it unconditionally. Why a key keeps the hash it was first written with
is in `docs/adr/0003-the-application-never-applies-ddl.md`.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.store.conn import DEFAULT_SCHEMA
from app.store.migrate import emit_sql, entry_hashes

SCHEMA_BASELINE = Path(__file__).resolve().parents[1] / ".schema-baseline.json"


class RecordedEntryRemoved(RuntimeError):
    """An entry in the baseline is gone from `ddl.py`, so regenerating would forget it."""


def merged_baseline(previous: dict[str, str], current: dict[str, str]) -> dict[str, str]:
    """`current`, except that a key already in `previous` keeps the hash it was recorded with.

    Raises `RecordedEntryRemoved` when a recorded key is gone from `current`, rather than
    dropping it: every database that applied that key would report one this build does not
    carry, and `check` refuses to serve any of them.
    """
    removed = sorted(set(previous) - set(current))
    if removed:
        raise RecordedEntryRemoved(
            f"{removed} is recorded in {SCHEMA_BASELINE.name} and gone from app/store/ddl.py. "
            f"Every database that applied it reports a key this build does not carry, and "
            f"`check` refuses to serve them. Restore the entry, or -- only if it never reached "
            f"a database -- delete its line from {SCHEMA_BASELINE.name} in the same commit."
        )
    return {key: previous.get(key, entry_hash) for key, entry_hash in current.items()}


def main() -> None:
    if len(sys.argv) != 2:
        usage = f"usage: schema.py <output-path>   (rewrites {SCHEMA_BASELINE.name} too)"
        print(usage, file=sys.stderr)
        raise SystemExit(2)
    Path(sys.argv[1]).write_text(emit_sql(DEFAULT_SCHEMA))
    previous = json.loads(SCHEMA_BASELINE.read_text())
    entries = merged_baseline(previous, entry_hashes(DEFAULT_SCHEMA))
    SCHEMA_BASELINE.write_text(json.dumps(entries, indent=2) + "\n")


if __name__ == "__main__":
    main()
