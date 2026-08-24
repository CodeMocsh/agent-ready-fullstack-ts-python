"""The comment gate, tested the way the frontend's is: a table in both directions.

`devtools/comments.py` is what makes *Zero comments* a rule rather than a preference, and it
is the gate whose failure is quietest -- a parser that misses a `#` lets the comment land, and
nothing else in either half is looking. Every trap here is the same one: it tokenizes rather
than greps, so a `#` inside a string is not a comment, a shebang on the first line is not one,
and a docstring is never one. This file is its own proof of the first -- every offending
sample below is a string literal, and `make lint` reads this file too.
"""

from pathlib import Path

from devtools.comments import offenders

BANNED = [
    "trailing = 1  # a note",
    "# a whole line",
    "value = 2  # noqa: E501",
    "other = 3  # type: ignore",
]
"""The suppressions are the half most worth refusing: each one is a threshold decision taken
silently at the point of pain, and the rule turns it into a fix or a reviewable line."""

ALLOWED = [
    '"""A docstring is documentation, not a comment."""',
    'anchor = "https://example.com/#section"',
    "colour = '#ffffff'",
    'formatted = f"{colour}#{anchor}"',
    "divided = 10 // 2",
]


def reported(tmp_path: Path, lines: list[str]) -> list[tuple[int, str]]:
    path = tmp_path / "sample.py"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return list(offenders(path))


def test_every_comment_is_reported_with_the_line_it_is_on(tmp_path: Path) -> None:
    found = reported(tmp_path, BANNED)

    assert [line for line, _ in found] == [1, 2, 3, 4]
    assert [text for _, text in found] == [
        "# a note",
        "# a whole line",
        "# noqa: E501",
        "# type: ignore",
    ]


def test_a_hash_inside_a_string_is_not_a_comment(tmp_path: Path) -> None:
    """The whole reason this reads tokens. A regular expression over the text reports every
    line here, and a project that cannot write a URL fragment turns the gate off."""
    assert reported(tmp_path, ALLOWED) == []


def test_a_shebang_is_allowed_on_the_first_line_and_nowhere_else(tmp_path: Path) -> None:
    """An executable directive, not an explanation -- but only where the kernel reads it.
    Anywhere below, `#!` is a comment wearing a hat."""
    assert reported(tmp_path, ["#!/usr/bin/env python", "value = 1"]) == []

    found = reported(tmp_path, ["value = 1", "#!/usr/bin/env python"])

    assert found == [(2, "#!/usr/bin/env python")]


def test_a_file_that_cannot_be_tokenized_reports_nothing(tmp_path: Path) -> None:
    """Half-written source is a syntax error somebody is already looking at. The gate stays
    quiet rather than adding a tokenizer traceback to it -- ruff is what reports that file."""
    assert reported(tmp_path, ["def broken(", "# and a comment below it"]) == []
