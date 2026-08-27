#!/usr/bin/env python3

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

LINKS = Path(__file__).resolve().parent / "links.py"

A_TREE_THE_SWEEP_ACCEPTS = {
    "README.md": "# Front door\n\n[a](a.md)\n",
    "a.md": "# A\n\n[x](docs/x.md)\n",
    "docs/x.md": "# X\n",
}


class Case(NamedTuple):
    label: str
    files: dict[str, str | bytes]
    exits: int
    says: tuple[str, ...] = ()
    never_says: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    from_a_repository: bool = False
    on_its_own: bool = False


CASES = (
    Case("a link to something that is not a document", {"a.md": "[s](devtools/gone.sh)\n"}, 1,
         says=("a.md names devtools/gone.sh",)),
    Case("a path in prose", {"a.md": "see `docs/gone.md` for more\n"}, 1,
         says=("a.md names docs/gone.md",)),
    Case("a decision by number", {"a.md": "see `docs/adr/0009`\n"}, 1,
         says=("a.md names docs/adr/0009",)),
    Case("a document rendered before it exists", {"a.md": "see `t.md.jinja`\n"}, 1,
         says=("a.md names t.md.jinja",)),
    Case("a path in a Makefile", {"Makefile": "# see docs/gone.md\n"}, 1,
         says=("Makefile names docs/gone.md",)),
    Case("a path in python", {"m.py": 'DOC = "docs/gone.md"\n'}, 1,
         says=("m.py names docs/gone.md",)),

    Case("a url that ends in a country code", {"a.md": "[c](https://agents.md)\n"}, 0,
         never_says=("names agents.md",)),
    Case("a jinja expression inside a link", {"a.md": "[x]({{base}}/docs/gone.md)\n"}, 0,
         never_says=("docs/gone.md",)),
    Case("a shell variable", {"a.md": 'run `"$REPO/docs/gone.md"`\n'}, 0,
         never_says=("docs/gone.md",)),
    Case("a placeholder filename", {"a.md": "name it `NNNN-what-was-decided.md`\n"}, 0,
         never_says=("NNNN",)),
    Case("an anchor with no path", {"a.md": "[s](#a-heading)\n"}, 0, never_says=("a-heading",)),
    Case("an address", {"a.md": "[m](mailto:someone@example.com)\n"}, 0, never_says=("mailto",)),
    Case("a path that resolves", {"a.md": "[up](./docs/x.md)\n"}, 0),
    Case("a bare filename the tree answers", {"a.md": "the layout lists `x.md`\n"}, 0),
    Case("a path relative to a directory this does not have",
         {"sub/deep/m.py": 'DOC = "../../x.md"\n'}, 0),
    Case("anything under a directory the sweep skips, listed by git",
         {"node_modules/pkg/readme.md": "[gone](docs/gone.md)\n"}, 0,
         from_a_repository=True, never_says=("docs/gone.md",)),
    Case("a tree listed by git rather than walked", {}, 0, from_a_repository=True),

    Case("a name the citing file's own directory answers",
         {"README.md": "[t](only.md)\n[n](docs/note.md)\n", "only.md": "# Root only\n",
          "docs/note.md": "# Note\n\n[o](only.md)\n", "docs/only.md": "# Docs only\n"}, 0),
    Case("a name only the project root answers unambiguously",
         {"README.md": "[t](other/docs/twin.md)\n[n](sub/note.md)\n",
          "other/docs/twin.md": "# Other twin\n", "docs/twin.md": "# Docs twin\n",
          "sub/note.md": "# Note\n\n[x](docs/twin.md)\n"}, 0),
    Case("a decision number that is a prefix of another",
         {"a.md": "see `docs/adr/0001`\n", "docs/adr/0001-real.md": "# Real\n",
          "docs/adr/00012-other.md": "# Other\n",
          "README.md": "[o](docs/adr/00012-other.md)\n"}, 0),
    Case("a decision number a file that is not a document also carries",
         {"a.md": "see `docs/adr/0002`\n", "docs/adr/0002-real.md": "# Real\n",
          "docs/adr/0002-notes.txt": "notes\n"}, 0),
    Case("a decision two trees answer",
         {"a.md": "see `docs/adr/0003`\n", "docs/adr/0003-here.md": "# Here\n",
          "sub/docs/adr/0003-there.md": "# There\n"}, 1, says=("cited only by number",)),

    Case("a document nothing names", {"docs/lonely.md": "# Lonely\n"}, 1,
         says=("orphan: docs/lonely.md",)),
    Case("a document that only names itself",
         {"docs/selfish.md": "# Selfish\n\nsee `docs/selfish.md`\n"}, 1,
         says=("orphan: docs/selfish.md",)),
    Case("a document the caller allows to be an orphan", {"docs/lonely.md": "# Lonely\n"}, 0,
         flags=("--allow-orphan", "docs/lonely.md")),
    Case("everything under a directory the caller excludes",
         {"skipme/note.md": "[gone](docs/gone.md)\n"}, 0,
         flags=("--exclude", "skipme"), never_says=("docs/gone.md",)),

    Case("a file of bytes", {"blob.dat": b"payload\x00 docs/gone.md\n"}, 0,
         says=("not UTF-8 text",), never_says=("blob.dat names",)),
    Case("a file that is not utf-8",
         {"notes.txt": "caf\xe9 - see docs/gone.md\n".encode("latin-1")}, 1,
         says=("not UTF-8 text", "notes.txt names docs/gone.md")),

    Case("a tree with nothing in it", {}, 1, says=("checked nothing",), on_its_own=True),
    Case("a tree of documents that name nothing", {"only.md": "# Only\n"}, 1,
         says=("checked nothing",), flags=("--allow-orphan", "only.md"), on_its_own=True),
    Case("anything under a directory the sweep skips, walked",
         {"node_modules/pkg/readme.md": "[gone](docs/gone.md)\n"}, 0,
         never_says=("docs/gone.md",)),
    Case("anything git is told to ignore",
         {".gitignore": "ignored/\n", "ignored/note.md": "[gone](docs/gone.md)\n"}, 0,
         from_a_repository=True, never_says=("docs/gone.md",)),
    Case("a tree of names with no document among them", {"Makefile": "# see a.md\n"}, 1,
         says=("checked nothing",), on_its_own=True),
    Case("a flag this does not accept", {}, 1, says=("does not accept",), flags=("--exclud", "x")),
    Case("a flag with nothing after it", {}, 1, says=("nothing after",), flags=("--exclude",)),
)


def write_tree(root: Path, case: Case) -> None:
    files: dict[str, str | bytes] = {} if case.on_its_own else dict(A_TREE_THE_SWEEP_ACCEPTS)
    for name, body in case.files.items():
        grown = files.get(name)
        if isinstance(body, bytes) or grown is None:
            files[name] = body
        else:
            files[name] = str(grown) + body
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            path.write_bytes(body)
        else:
            path.write_text(body, encoding="utf-8")


def sweep(case: Case) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as scratch:
        write_tree(Path(scratch), case)
        if case.from_a_repository:
            subprocess.run(["git", "-C", scratch, "init", "-q"], check=True)
        allowance = () if case.on_its_own else ("--allow-orphan", "README.md")
        done = subprocess.run(
            [sys.executable, str(LINKS), scratch, *allowance, *case.flags],
            capture_output=True,
            text=True,
            check=False,
        )
        return done.returncode, done.stderr


def main() -> int:
    failures: list[str] = []
    for case in CASES:
        code, output = sweep(case)
        if code != case.exits:
            failures.append(f"{case.label}: exited {code}, wanted {case.exits}:\n{output}")
        for wanted in case.says:
            if wanted not in output:
                failures.append(f"{case.label}: never said {wanted!r}:\n{output}")
        for unwanted in case.never_says:
            if unwanted in output:
                failures.append(f"{case.label}: said {unwanted!r}, and should not have:\n{output}")

    for line in failures:
        print(f"check:   {line}", file=sys.stderr)
    if failures:
        print(
            "check: links.py no longer catches what it was built to catch. "
            "A pattern that stopped matching degrades the sweep without failing it.",
            file=sys.stderr,
        )
        return 1
    print(f"links: {len(CASES)} cases, every citation spelling caught and every non-path ignored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
