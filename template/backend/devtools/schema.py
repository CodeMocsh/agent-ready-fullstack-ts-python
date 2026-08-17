import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.store.conn import DEFAULT_SCHEMA
from app.store.migrate import emit_sql


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: schema.py <output-path>", file=sys.stderr)
        raise SystemExit(2)
    Path(sys.argv[1]).write_text(emit_sql(DEFAULT_SCHEMA))


if __name__ == "__main__":
    main()
