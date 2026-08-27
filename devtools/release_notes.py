#!/usr/bin/env python3

import re
import sys
from pathlib import Path
from typing import NoReturn

VERSION_HEADING = re.compile(r"^## (v\d+\.\d+\.\d+\S*)")
ANY_HEADING = re.compile(r"^## ")
MODES = ("--version", "--notes")


def refuse(message: str) -> NoReturn:
    print(f"release: {message}", file=sys.stderr)
    raise SystemExit(1)


def claimed_release(changelog: Path) -> tuple[str, str]:
    if not changelog.is_file():
        refuse(f"{changelog} does not exist, and it is what decides the version.")
    lines = changelog.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        heading = VERSION_HEADING.match(line)
        if heading is None:
            continue
        version = heading.group(1)
        body: list[str] = []
        for following in lines[index + 1 :]:
            if ANY_HEADING.match(following):
                break
            body.append(following)
        notes = "\n".join(body).strip()
        if not notes:
            refuse(
                f"{version} is the version {changelog} claims, and its section is empty, "
                "so the release would say nothing."
            )
        return version, notes
    refuse(
        f"{changelog} carries no '## vX.Y.Z' heading, so it names no version to release. "
        "The topmost one is what this repo claims to be at."
    )


def main() -> int:
    argv = sys.argv[1:]
    if len(argv) != 2 or argv[1] not in MODES:
        refuse(f"usage: release_notes.py <changelog> [{' | '.join(MODES)}]")
    version, notes = claimed_release(Path(argv[0]))
    print(version if argv[1] == "--version" else notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
