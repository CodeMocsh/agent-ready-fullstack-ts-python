"""The comment gate, tested the way the frontend's is: a table in both directions.

`devtools/comments.py` is what makes *Zero comments* a rule rather than a preference, and it
is the gate whose failure is quietest -- a parser that misses a `#` lets the comment land, and
nothing else in either half is looking. Every trap here is the same one: it tokenizes rather
than greps, so a `#` inside a string is not a comment, a shebang on the first line is not one,
and a docstring is never one. This file is its own proof of the first -- every offending
sample below is a string literal, and `make lint` reads this file too.
"""

import subprocess
from pathlib import Path

import pytest

from devtools.comments import main, offenders

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


SCRIPT_BANNED = [
    "# a whole line",
    "echo hi  # a note",
    "run;# after a separator",
    "cat <<< word  # after a here-string",
    "shifted=$((a << b))  # after an arithmetic shift",
    "quoted=$'it\\'s'  # after an ANSI-C quote",
]

SCRIPT_ALLOWED = [
    'echo "# in double quotes"',
    "echo '#' $# ${#name} ${name#prefix}",
    "url=https://example.com/#section",
    "cat <<'EOF'",
    "# a heredoc body is data",
    "EOF",
    "node -e '",
    "# a string that spans lines",
    "'",
    'nested="$(echo "a # b")"',
    "cat <<\\EOF",
    "# an escaped heredoc body is data",
    "EOF",
    "cat <<-EOF",
    "\t# a tab-stripped heredoc body is data",
    "\tEOF",
    "cat <<EOF",
    "\tEOF",
    "# only a dash heredoc ends at an indented terminator",
    "EOF",
    "out=`echo '#'`",
    'message="`date` # inside double quotes"',
    "fallback=${name:- #}",
]

MAKEFILE_BANNED = [
    "# a make comment",
    "VALUE = 1 # trailing",
    "target:",
    "\t# a recipe line the shell reads as a comment",
]

MAKEFILE_ALLOWED = [
    "define BODY",
    "# a define body is data",
    "endef",
    "override define OTHER",
    "# an override define body is data",
    "endef",
    "continued:",
    "\t@echo 'a quote that a continued \\",
    "\t# recipe line still holds'",
    "target:",
    "\t@echo '#' $$# done",
    "ESCAPED = \\#",
]


def reported(tmp_path: Path, lines: list[str], name: str = "sample.py") -> list[tuple[int, str]]:
    path = tmp_path / name
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


def test_a_file_that_cannot_be_tokenized_is_refused_rather_than_reported_clean(
    tmp_path: Path,
) -> None:
    """A file this cannot parse is a file it cannot clear, and there is a comment in the
    sample below to prove what quiet would have cost.

    It used to return nothing here, on the reasoning that a syntax error is already somebody's
    problem and ruff is what names it. That holds inside `make lint`, where ruff runs first --
    and nowhere else. `devtools/comments.py` is dependency-free so that it can run before
    either half is installed, which is exactly the run where no ruff exists to report anything,
    and a scan that answers "no comments" over a file it never read is the failure this whole
    module is written against. It names the file and exits; the traceback the old reasoning
    wanted to avoid is not what it produces."""
    with pytest.raises(SystemExit) as refusal:
        reported(tmp_path, ["def broken(", "# and a comment below it"])

    assert refusal.value.code == 2


def test_every_comment_in_a_script_is_reported_with_the_line_it_is_on(tmp_path: Path) -> None:
    found = reported(tmp_path, SCRIPT_BANNED, "sample.sh")

    assert [line for line, _ in found] == [1, 2, 3, 4, 5, 6]
    assert found[1] == (2, "# a note")


def test_a_hash_a_script_quotes_expands_or_feeds_a_heredoc_is_not_a_comment(
    tmp_path: Path,
) -> None:
    assert reported(tmp_path, SCRIPT_ALLOWED, "sample.sh") == []


def test_a_script_allows_a_shebang_on_the_first_line_and_nowhere_else(tmp_path: Path) -> None:
    assert reported(tmp_path, ["#!/bin/sh", "set -eu"], "sample.sh") == []
    assert reported(tmp_path, ["set -eu", "#!/bin/sh"], "sample.sh") == [(2, "#!/bin/sh")]


def test_every_comment_in_a_makefile_is_reported_in_and_out_of_a_recipe(tmp_path: Path) -> None:
    found = reported(tmp_path, MAKEFILE_BANNED, "Makefile")

    assert [line for line, _ in found] == [1, 2, 4]


def test_a_hash_a_makefile_escapes_or_a_recipe_quotes_is_not_a_comment(tmp_path: Path) -> None:
    assert reported(tmp_path, MAKEFILE_ALLOWED, "Makefile") == []


def test_a_folder_scan_reads_hooks_and_templated_scripts_and_skips_a_file_of_no_known_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".githooks").mkdir()
    (tmp_path / ".githooks" / "pre-push").write_text("make check  # a hook\n", encoding="utf-8")
    (tmp_path / "gate.sh.jinja").write_text("set -eu\n# a template\n", encoding="utf-8")
    (tmp_path / "pre-commit").write_text("#!/bin/sh\n# a shebang\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("# not a script\n", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["comments.py", str(tmp_path)])

    assert main() == 1
    reports = [line for line in capsys.readouterr().out.splitlines() if "  #" in line]
    assert reports == [
        f"{tmp_path / '.githooks' / 'pre-push'}:1  # a hook",
        f"{tmp_path / 'gate.sh.jinja'}:2  # a template",
        f"{tmp_path / 'pre-commit'}:2  # a shebang",
    ]


@pytest.mark.parametrize(
    ("name", "lines"),
    [("open.sh", ['echo "never closed']), ("body.sh", ["cat <<EOF", "never ended"])],
    ids=["quote", "heredoc"],
)
def test_a_script_that_ends_inside_a_quote_or_a_heredoc_is_refused(
    tmp_path: Path, name: str, lines: list[str]
) -> None:
    with pytest.raises(SystemExit) as refusal:
        reported(tmp_path, [*lines, "# and a comment below it"], name)

    assert refusal.value.code == 2


def test_pod_in_a_perl_script_is_reported(tmp_path: Path) -> None:
    found = reported(tmp_path, ["#!/usr/bin/perl", "=pod", "a note", "=cut"], "sample.pl")

    assert found == [(2, "=pod"), (4, "=cut")]


def test_a_named_file_of_no_known_kind_is_refused_rather_than_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "notes.txt").write_text("# a note\n", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["comments.py", str(tmp_path / "notes.txt")])

    with pytest.raises(SystemExit) as refusal:
        main()

    assert refusal.value.code == 2


@pytest.mark.parametrize(
    ("name", "lines"),
    [
        ("sample.pl", ["#!/usr/bin/perl", "sub f {# a note"]),
        ("tool", ["#!/usr/bin/env bash", "# a note"]),
    ],
    ids=["perl-after-a-brace", "env-shebang"],
)
def test_a_comment_is_reported_whatever_shape_the_script_has(
    tmp_path: Path, name: str, lines: list[str]
) -> None:
    assert reported(tmp_path, lines, name) == [(2, "# a note")]


def test_a_recipe_line_that_ends_inside_a_quote_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as refusal:
        reported(tmp_path, ["all:", "\techo 'never closed", "\t# a comment"], "Makefile")

    assert refusal.value.code == 2


def test_a_script_under_an_interpreter_this_cannot_read_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as refusal:
        reported(tmp_path, ["#!/usr/bin/env node", "// a note"], "tool")

    assert refusal.value.code == 2


def test_the_tree_scan_reads_every_file_git_tracks_or_would_track_and_nothing_it_ignores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    (tmp_path / "committed.sh").write_text("# committed\n", encoding="utf-8")
    git("add", "committed.sh")
    git("-c", "user.email=a@b.c", "-c", "user.name=a", "commit", "-qm", "one")
    (tmp_path / "anywhere").mkdir()
    (tmp_path / "anywhere" / "hook.py").write_text("x = 1  # untracked\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("ignored.sh\n", encoding="utf-8")
    (tmp_path / "ignored.sh").write_text("# ignored\n", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["comments.py", "--tree", str(tmp_path)])

    assert main() == 1
    reports = [line for line in capsys.readouterr().out.splitlines() if "  #" in line]
    assert reports == [
        f"{tmp_path / 'anywhere' / 'hook.py'}:1  # untracked",
        f"{tmp_path / 'committed.sh'}:1  # committed",
    ]
