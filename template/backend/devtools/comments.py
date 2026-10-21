import argparse
import io
import re
import subprocess
import sys
import tokenize
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple, NoReturn, final

FIX = """
AGENTS.md bans them, and the rule is not about tidiness: an explanation beside the
code is the copy that goes stale silently. Rationale belongs in the commit message,
an architectural decision also in docs/adr/, and a contract in a name or a type.

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


HEREDOC = re.compile(r"<<(-?)\s*(\\?)(['\"]?)([A-Za-z_]\w*)\3")
POD = re.compile(r"=[A-Za-z]")
DEFINE = re.compile(r"\s*(override\s+)?define\b")
WORD_BREAKS = " \t;|&()`"
PERL_DATA = "$@_\\"
SUFFIXES = {".py": "python", ".sh": "shell", ".bash": "shell", ".pl": "perl", ".mk": "make"}
MAKEFILES = {"Makefile", "makefile", "GNUmakefile"}
INTERPRETERS = {"sh": "shell", "bash": "shell", "dash": "shell", "perl": "perl"}
QUOTES = {"'", '"', "$'"}
OPENERS = [
    ("$((", "(("),
    ("((", "(("),
    ("$(", "$("),
    ("${", "${"),
    ("$'", "$'"),
    ("'", "'"),
    ('"', '"'),
]


def interpreter_of(path: Path) -> str | None:
    with path.open("rb") as opened:
        first = opened.readline(200)
    if not first.startswith(b"#!"):
        return None
    words = first[2:].decode("utf-8", "replace").split()
    if words and Path(words[0]).name == "env":
        words = words[1:]
    program = Path(words[0]).name if words else ""
    if program.startswith("python"):
        return "python"
    if program not in INTERPRETERS:
        fail(f"{path} runs under {program!r}, a language this cannot read. Give it a suffix.")
    return INTERPRETERS[program]


def kind(path: Path) -> str | None:
    """The language `path` is read as, or None for a file that is not source."""
    bare = path.with_suffix("") if path.suffix == ".jinja" else path
    if bare.name in MAKEFILES or bare.suffix == ".mk":
        return "make"
    if bare.suffix:
        return SUFFIXES.get(bare.suffix)
    found = interpreter_of(path)
    if found is None and path.parent.name == ".githooks":
        return "shell"
    return found


@final
class Shell:
    """Reads shell one line at a time: where a comment starts, and which quotes, substitutions
    and heredoc bodies are still open. `close` refuses a script that ends with one open."""

    def __init__(self, path: Path, *, perl: bool = False) -> None:
        self.path = path
        self.perl = perl
        self.open: list[str] = []
        self.bodies: list[tuple[str, bool, bool]] = []
        self.joined = False

    def comment_in(self, line: str) -> int | None:
        if self.bodies:
            terminator, tabs, expands = self.bodies[0]
            ends = not self.joined and (line.lstrip("\t") if tabs else line) == terminator
            self.joined = expands and line.endswith("\\")
            if ends:
                self.bodies.pop(0)
            return None
        index = 0
        while index < len(line):
            top = self.open[-1] if self.open else ""
            if top in QUOTES:
                index = self.quoted(line, index, top)
            elif top in ("((", "a("):
                index = self.arithmetic(line, index, top)
            elif top == "${" and line[index] in "#}":
                index = self.parameter(line[index], index)
            elif line[index] == "#" and self.starts_comment(line, index):
                return index
            else:
                index = self.code(line, index, top)
        return None

    def starts_comment(self, line: str, index: int) -> bool:
        if index == 0:
            return True
        before = line[index - 1]
        if self.perl:
            return not (before.isalnum() or before in PERL_DATA)
        return before in WORD_BREAKS

    def parameter(self, char: str, index: int) -> int:
        if char == "}":
            self.open.pop()
        return index + 1

    def enter(self, context: str, index: int) -> int:
        self.open.append(context)
        return index

    def quoted(self, line: str, index: int, top: str) -> int:
        char = line[index]
        if char == "\\" and top != "'":
            return index + 2
        if top == '"' and line.startswith("$(", index):
            return self.code(line, index, top)
        if top == '"' and char == "`":
            return self.enter("`", index + 1)
        if char == ("'" if top in ("'", "$'") else '"'):
            self.open.pop()
        return index + 1

    def arithmetic(self, line: str, index: int, top: str) -> int:
        if top == "((" and line.startswith("))", index):
            self.open.pop()
            return index + 2
        if line[index] == "(":
            return self.enter("a(", index + 1)
        if top == "a(" and line[index] == ")":
            self.open.pop()
        return index + 1

    def code(self, line: str, index: int, top: str) -> int:
        if line.startswith("<<<", index):
            return index + 3
        if heredoc := HEREDOC.match(line, index):
            expands = not heredoc.group(2) and not heredoc.group(3)
            self.bodies.append((heredoc.group(4), heredoc.group(1) == "-", expands))
            return heredoc.end()
        for token, context in OPENERS:
            if line.startswith(token, index):
                return self.enter(context, index + len(token))
        return self.punctuation(line[index], index, top)

    def punctuation(self, char: str, index: int, top: str) -> int:
        if char == "\\":
            return index + 2
        if char == "`" and top == "`":
            self.open.pop()
        elif char in "`(":
            return self.enter(char, index + 1)
        elif char == ")" and top in ("(", "$("):
            self.open.pop()
        return index + 1

    def close(self) -> None:
        if self.open or self.bodies:
            fail(
                f"{self.path} ends inside {self.open[-1] if self.open else 'a heredoc'}, so this "
                f"cannot tell where its comments are, and reporting it clean is the one answer "
                f"certain to be wrong. Fix the script, or the scan in devtools/comments.py."
            )


class Comment(NamedTuple):
    """A comment the scan found. `column` is `None` for POD, which runs on past the line that
    opens it."""

    line: int
    column: int | None
    text: str


AMBIGUOUS_BREAKS = "\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029\ufeff"


def shell_comments(path: Path) -> Iterator[Comment]:
    perl = kind(path) == "perl"
    shell = Shell(path, perl=perl)
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if number == 1 and line.startswith("#!"):
            continue
        if perl and POD.match(line):
            yield Comment(number, None, line.strip())
            continue
        start = shell.comment_in(line)
        if start is not None:
            yield Comment(number, start, line[start:].strip())
    shell.close()


def make_comment_in(line: str) -> int | None:
    """Make ends a line that is not a recipe at its first `#` that has no backslash before it."""
    found = re.search(r"(?<!\\)#", line)
    return found.start() if found else None


def makefile_comments(path: Path) -> Iterator[Comment]:
    shell = Shell(path)
    continued = defining = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if defining:
            defining = line.strip() != "endef"
            continue
        recipe = line.startswith("\t") or continued
        defining = not recipe and bool(DEFINE.match(line))
        if recipe:
            start = shell.comment_in(line)
            continued = line.endswith("\\")
            if not continued:
                shell.close()
        else:
            start = None if defining else make_comment_in(line)
        if start is not None:
            yield Comment(number, start, line[start:].strip())


def comments(path: Path) -> Iterator[Comment]:
    language = kind(path)
    if language == "make":
        yield from makefile_comments(path)
        return
    if language != "python":
        yield from shell_comments(path)
        return
    for token in tokens_of(path):
        if token.type != tokenize.COMMENT:
            continue
        if token.start == (1, 0) and token.string.startswith("#!"):
            continue
        yield Comment(token.start[0], token.start[1], token.string.strip())


def offenders(path: Path) -> Iterator[tuple[int, str]]:
    for number, _, text in comments(path):
        yield number, text


def without(source: str, found: list[Comment], path: Path) -> str:
    """`source` with each comment in `found` removed: the whole line when nothing precedes it,
    and otherwise the comment and the space before it, so a Makefile value before a comment
    loses its trailing spaces. A whole line after a line that ends in a
    backslash becomes an empty line. Exits, and changes nothing, on a comment it cannot remove
    without changing what the code does."""
    if any(char in source for char in AMBIGUOUS_BREAKS):
        fail(
            f"{path} holds a form feed, a byte order mark or a Unicode line break, so the line "
            f"numbers the scan reports may not be the lines --strip cuts. Remove its comments by hand."
        )
    lines = source.splitlines(keepends=True)
    make = kind(path) == "make"
    for number, start, text in reversed(found):
        line = lines[number - 1]
        ending = line[len(line.rstrip("\r\n")) :]
        kept = "" if start is None else line[:start].rstrip()
        if start is None or (make and text.endswith("\\")):
            fail(
                f"{path}:{number} holds {text!r}, which runs on past its line, so --strip cannot "
                f"tell where it ends. Remove it by hand, then run --strip again."
            )
        if kept.endswith("\\"):
            fail(
                f"{path}:{number} keeps a backslash at the end of the line once {text!r} is gone, "
                f"which joins the next line to it. Remove the comment by hand."
            )
        joined = number > 1 and lines[number - 2].rstrip("\r\n").endswith("\\")
        lines[number - 1] = kept + ending if kept or joined else ""
    return "".join(lines)


def strip(path: Path) -> int:
    """Removes every comment the scan finds in `path`, and answers how many. Restores the file
    and exits when the result no longer reads, or still holds one."""
    original = path.read_bytes()
    found = list(comments(path))
    if not found:
        return 0
    path.write_bytes(without(original.decode("utf-8"), found, path).encode("utf-8"))
    try:
        left = list(offenders(path))
    except SystemExit:
        path.write_bytes(original)
        raise
    if left:
        path.write_bytes(original)
        fail(f"{path} still holds {len(left)} comment(s) after --strip. Remove them by hand.")
    return len(found)


def tracked(root: Path) -> list[Path]:
    """Every source file and script under `root` that git tracks or would track, so no list of
    folders can leave one unread."""
    command = [
        "git",
        "-C",
        str(root),
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
    ]
    try:
        listed = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        fail("git is not on PATH, and --tree reads the files git sees. Install git.")
    if listed.returncode != 0:
        fail(f"git cannot list the files under {root}: {listed.stderr.strip()}")
    files = (root / name for name in sorted(set(listed.stdout.split("\0"))) if name)
    return [one for one in files if one.is_file() and kind(one) is not None]


def named_paths(roots: list[str]) -> Iterator[Path]:
    for root in map(Path, roots):
        if not root.exists():
            fail(
                f"{root} is not in this tree. `rglob` answers an empty list for a path that is "
                f"not there, so a scan of it reports clean and a renamed folder goes unread. "
                f"Correct the path."
            )
        if root.is_file() and kind(root) is None:
            fail(f"{root} is neither source nor a script, so this cannot read it. Name another.")
        candidates = [root] if root.is_file() else sorted(root.rglob("*"))
        yield from (one for one in candidates if one.is_file() and kind(one) is not None)


def main() -> int:
    parser = argparse.ArgumentParser(description="Refuse comments in source and scripts.")
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--tree", type=Path, help="read every source file git sees under this root")
    parser.add_argument("--strip", action="store_true", help="remove each comment it would refuse")
    args = parser.parse_args()
    if not args.paths and args.tree is None:
        parser.error("name a path, or --tree ROOT")

    found = 0
    seen = tracked(args.tree) if args.tree else []
    paths = [*named_paths(args.paths), *seen]
    if args.strip:
        return stripped_all(paths)
    for path in paths:
        for line, text in offenders(path):
            print(f"{path}:{line}  {text}")
            found += 1

    if found:
        print(f"\nFAIL: {found} comment{'' if found == 1 else 's'} above.{FIX}")
    return 1 if found else 0


def stripped_all(paths: list[Path]) -> int:
    removed = 0
    for path in paths:
        count = strip(path)
        if count:
            print(f"{path}: removed {count}", flush=True)
        removed += count
    print(
        f"\nRemoved {removed} comment(s). Read the diff before you commit it: a "
        f"reason a comment held belongs in the commit message, and a contract in a name."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
