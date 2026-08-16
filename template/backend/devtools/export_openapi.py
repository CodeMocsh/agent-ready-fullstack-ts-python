import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: export_openapi.py <output-path>", file=sys.stderr)
        raise SystemExit(2)
    destination = Path(sys.argv[1])
    destination.write_text(json.dumps(app.openapi(), indent=2) + "\n")


if __name__ == "__main__":
    main()
