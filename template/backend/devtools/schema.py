"""Regenerates what `make schema` owns: the SQL script, and the entry lock.

`.schema-entries.json` ships committed and `check_template.sh` requires it, so a missing one is
a failure and this reads it unconditionally. Why a key keeps the hash it was locked at is in
`docs/adr/0003-the-application-never-applies-ddl.md`.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.store.conn import DEFAULT_SCHEMA
from app.store.migrate import emit_sql, entry_hashes

ENTRY_LOCK = Path(__file__).resolve().parents[1] / ".schema-entries.json"


def merged_lock(previous: dict[str, str], current: dict[str, str]) -> dict[str, str]:
    """`current`, except that a key already in `previous` keeps the hash it was locked at."""
    return {key: previous.get(key, entry_hash) for key, entry_hash in current.items()}


def main() -> None:
    if len(sys.argv) != 2:
        print(f"usage: schema.py <output-path>   (rewrites {ENTRY_LOCK.name} too)", file=sys.stderr)
        raise SystemExit(2)
    Path(sys.argv[1]).write_text(emit_sql(DEFAULT_SCHEMA))
    previous = json.loads(ENTRY_LOCK.read_text())
    entries = merged_lock(previous, entry_hashes(DEFAULT_SCHEMA))
    ENTRY_LOCK.write_text(json.dumps(entries, indent=2) + "\n")


if __name__ == "__main__":
    main()
