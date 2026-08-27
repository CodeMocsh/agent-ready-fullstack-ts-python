import argparse
import io
import sys
import tokenize
from collections.abc import Iterator
from pathlib import Path
from typing import NoReturn

FIX = """
AGENTS.md bans them, and the rule is not about tidiness: an explanation beside the
code is the copy that goes stale silently. Rationale belongs in the commit message,
a decision in docs/adr/, and a contract in a name or a type.

A suppression is the half most worth refusing. `# noqa` and `# type: ignore` are
threshold decisions taken silently at the point of pain; make it a fix in the code,
or a reviewable line in pyproject.toml. Docstrings are not comments and are not
checked here.
"""


def fail(message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


def tokens_of(path: Path) -> list[tokenize.TokenInfo]:
    source = path.read_text(encoding="utf-8")
    try:
        return list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError) as error:
        fail(
            f"{path} does not tokenize ({error}), so this never read it. A file the scan "
            f"cannot parse is a file it cannot clear, and reporting it clean is the one "
            f"answer certain to be wrong. Fix the syntax."
        )


def offenders(path: Path) -> Iterator[tuple[int, str]]:
    for token in tokens_of(path):
        if token.type != tokenize.COMMENT:
            continue
        if token.start == (1, 0) and token.string.startswith("#!"):
            continue
        yield token.start[0], token.string.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Refuse comments in Python source.")
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args()

    found = 0
    for root in args.paths:
        if not Path(root).is_dir():
            fail(
                f"{root} is not a directory in this tree. `rglob` answers an empty list for a "
                f"path that is not there, so a scan of it reports clean and a renamed source "
                f"folder goes unread. Correct the path in devtools/lint.py."
            )
        for path in sorted(Path(root).rglob("*.py")):
            for line, text in offenders(path):
                print(f"{path}:{line}  {text}")
                found += 1

    if found:
        print(f"\nFAIL: {found} comment{'' if found == 1 else 's'} above.{FIX}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
