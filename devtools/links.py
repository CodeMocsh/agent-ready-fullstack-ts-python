#!/usr/bin/env python3

import re
import subprocess
import sys
from pathlib import Path

SKIPPED_DIRECTORIES = {
    ".git",
    "node_modules",
    ".venv",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
}

UNRESOLVABLE_UNTIL_SOMETHING_ELSE_RUNS = (
    re.compile(r"https?://\S+"),
    re.compile(r"\{\{.*?\}\}|\{%.*?%\}"),
    re.compile(r"\$\{?\w+\}?[\w./-]*"),
)

MARKDOWN_LINK = re.compile(r"\]\(([^)\s]+)\)")
DOCUMENT_PATH = re.compile(
    r"(?<![\w/])((?:\.{1,2}/)?(?:[\w.-]+/)*[\w.-]+\.md(?:\.jinja)?)(?![\w]|\.[A-Za-z])"
)
DECISION_STEM = re.compile(r"(?<![\w/])((?:[\w.-]+/)*adr/\d{4})(?![\w-])")
FILENAME_PLACEHOLDER = "NNNN"
REPLACEMENT = "\ufffd"
KNOWN_FLAGS = ("--exclude", "--allow-orphan")


def files_under(root: Path) -> list[Path]:
    inside_a_repository = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--git-dir"],
        capture_output=True,
        check=False,
    )
    if inside_a_repository.returncode == 0:
        listed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            check=True,
        )
        return [
            root / name
            for name in listed.stdout.splitlines()
            if (root / name).is_file()
            and SKIPPED_DIRECTORIES.isdisjoint(Path(name).parts)
        ]
    found: list[Path] = []
    unvisited = [root]
    while unvisited:
        for entry in sorted(unvisited.pop().iterdir()):
            if entry.is_dir():
                if entry.name not in SKIPPED_DIRECTORIES:
                    unvisited.append(entry)
            elif entry.is_file():
                found.append(entry)
    return found


def names_cited_in(text: str) -> set[str]:
    for pattern in UNRESOLVABLE_UNTIL_SOMETHING_ELSE_RUNS:
        text = pattern.sub(" ", text)
    cited = {match.group(1).split("#", 1)[0] for match in MARKDOWN_LINK.finditer(text)}
    cited |= {match.group(1) for match in DOCUMENT_PATH.finditer(text)}
    cited |= {match.group(1) for match in DECISION_STEM.finditer(text)}
    return {
        name
        for name in cited
        if name and FILENAME_PLACEHOLDER not in name and not name.startswith("mailto:")
    }


def answers_for(root: Path, every_file: list[Path], source: Path, name: str) -> set[Path]:
    for base in (source.parent, root):
        if (base / name).exists():
            return {(base / name).resolve()}
    tail = "/" + re.sub(r"^(\.{1,2}/)+", "", name)
    cited = Path(name)
    if cited.parent.name == "adr" and cited.name.isdigit():
        return {
            path.resolve()
            for path in every_file
            if f"{tail}-" in str(path) and path.suffix == ".md"
        }
    return {path.resolve() for path in every_file if str(path).endswith(tail)}


def main() -> int:
    argv = sys.argv[1:]
    unaccepted = [arg for arg in argv if arg.startswith("--") and arg not in KNOWN_FLAGS]
    if unaccepted:
        raise SystemExit(f"check: links.py does not accept {unaccepted}, so it refused to run.")

    valueless = [arg for index, arg in enumerate(argv) if arg in KNOWN_FLAGS and index + 1 == len(argv)]
    if valueless:
        raise SystemExit(f"check: links.py got {valueless} with nothing after it.")

    root = Path(argv[0]).resolve()

    def given(flag: str) -> set[str]:
        return {argv[index + 1] for index, arg in enumerate(argv) if arg == flag}

    allowed_orphans, excluded = given("--allow-orphan"), given("--exclude")

    every_file = files_under(root)
    swept = [
        path
        for path in every_file
        if not any(path.relative_to(root).is_relative_to(prefix) for prefix in excluded)
    ]

    unresolvable: list[str] = []
    not_text: list[str] = []
    reachable: set[Path] = set()
    cited_ambiguously: set[Path] = set()
    names_checked = 0
    for path in swept:
        content = path.read_bytes()
        holds_bytes_no_text_encoding_explains = b"\x00" in content
        text = "" if holds_bytes_no_text_encoding_explains else content.decode("utf-8", "replace")
        if holds_bytes_no_text_encoding_explains or REPLACEMENT in text:
            not_text.append(str(path.relative_to(root)))
        for name in sorted(names_cited_in(text)):
            names_checked += 1
            answers = answers_for(root, every_file, path, name)
            answered_by_one_other_file = len(answers) == 1 and path.resolve() not in answers
            if not answers:
                unresolvable.append(f"{path.relative_to(root)} names {name}, which exists nowhere")
            elif answered_by_one_other_file:
                reachable |= answers
            elif len(answers) > 1:
                cited_ambiguously |= answers

    documents = [path for path in swept if path.suffix == ".md"]
    if not names_checked or not documents:
        raise SystemExit(
            f"check: swept {len(swept)} files under {root} and found {names_checked} names "
            f"across {len(documents)} documents, so this check checked nothing. "
            "Verify the root, the --exclude prefixes, and that the project was generated."
        )

    unreachable = sorted(
        path
        for path in documents
        if path.resolve() not in reachable and str(path.relative_to(root)) not in allowed_orphans
    )

    for line in not_text:
        print(f"check:   not UTF-8 text, so a name inside it may be unchecked: {line}", file=sys.stderr)
    for line in unresolvable:
        print(f"check:   dead link: {line}", file=sys.stderr)
    for path in unreachable:
        near = " (cited only by number, which both trees answer)" if path.resolve() in cited_ambiguously else ""
        print(f"check:   orphan: {path.relative_to(root)} is named by no other file{near}", file=sys.stderr)
    if unresolvable or unreachable:
        print(
            "check: repoint the name, or delete the document and repoint what named it. "
            "An orphan is reachable by nobody, so the decision inside it gets made again.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
