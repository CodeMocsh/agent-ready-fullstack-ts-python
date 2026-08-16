#!/usr/bin/env python3

from __future__ import annotations

import json
import re
import sys
from pathlib import PurePosixPath

_FORK_BOMB = re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:?\s*&?\s*\}\s*;\s*:")
_RAW_DISK = re.compile(
    r"\bmkfs\b|\bdd\b[^\n]*\bof=/dev/(?:sd|nvme|hd|disk|mmcblk)|>\s*/dev/(?:sd|nvme|hd|disk)",
    re.IGNORECASE,
)
_RM_CALL_WITH_ARGUMENTS = re.compile(r"\brm\b([^\n;|&]+)")
_RM_TARGETS_MEANING_WIPE_EVERYTHING = {"/", "~", "$home", "${home}", "/*", "~/*", "."}

_ENV_FILENAME_SUFFIXES_THAT_ARE_SAFE = (".example", ".sample", ".template", ".dist")
_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "GitHub personal access token"),
    (re.compile(r"gho_[A-Za-z0-9]{20,}"), "GitHub OAuth token"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "GitHub fine-grained PAT"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}"), "OpenAI-style secret key"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key ID"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "Google API key"),
    (
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
        "private key",
    ),
]


def _deny_and_exit(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"[agent-guard] {reason}",
                }
            }
        )
    )
    sys.exit(0)


def _rm_is_recursive_forced_and_its_targets(arguments: str) -> tuple[bool, bool, list[str]]:
    """Read an rm call's flags without assuming they come first.

    "rm / -rf" deletes exactly what "rm -rf /" does, and "--" ends the flags
    rather than being one, so every word is classified in turn instead of
    matching a leading flag group. Short flags cluster ("-rf"); long ones are
    compared whole, so "--no-preserve-root" is not read as a recursive flag.
    """
    recursive = forced = False
    targets: list[str] = []
    flags_ended = False

    for word in arguments.split():
        if word == "--":
            flags_ended = True
        elif flags_ended or word == "-" or not word.startswith("-"):
            targets.append(word)
        elif word.startswith("--"):
            recursive = recursive or word == "--recursive"
            forced = forced or word == "--force"
        else:
            recursive = recursive or "r" in word
            forced = forced or "f" in word

    return recursive, forced, targets


def _catastrophic_rm(lowercased_command: str) -> str | None:
    for call in _RM_CALL_WITH_ARGUMENTS.finditer(lowercased_command):
        recursive, forced, targets = _rm_is_recursive_forced_and_its_targets(call.group(1))
        if not (recursive and forced):
            continue
        for raw_target in targets:
            target = raw_target.strip("\"'").rstrip("/")
            if target in _RM_TARGETS_MEANING_WIPE_EVERYTHING or target == "":
                return "rm -rf targeting a root/home/critical path."
    return None


def _dangerous_shell(command: str) -> str | None:
    lowercased = command.lower()

    if _FORK_BOMB.search(command):
        return "Fork bomb detected."
    if _RAW_DISK.search(lowercased):
        return "Raw-disk write/format command detected (mkfs/dd/redirect to /dev)."
    return _catastrophic_rm(lowercased)


def _dangerous_git(command: str) -> str | None:
    lowercased = command.lower()

    if re.search(r"\bgit\s+push\b", lowercased):
        forced = re.search(r"--force(?!-with-lease)\b", lowercased) or re.search(
            r"(?:^|\s)-[a-z]*f[a-z]*\b", lowercased
        )
        if forced and re.search(r"\b(?:main|master)\b", lowercased):
            return "Force-push to main/master. Use --force-with-lease on a feature branch."

    if re.search(r"\bgit\s+commit\b", lowercased) and "--no-verify" in lowercased:
        return "git commit --no-verify bypasses configured hooks."
    return None


def _dangerous_write(file_path: str, content: str) -> str | None:
    name = PurePosixPath(file_path).name.lower()
    if name.startswith(".env") and not name.endswith(_ENV_FILENAME_SUFFIXES_THAT_ARE_SAFE):
        return "Writing a .env file (likely secrets). Commit a .env.example instead."
    for pattern, label in _SECRET_PATTERNS:
        if pattern.search(content):
            return f"Possible hard-coded secret ({label}). Use an env var or secrets manager."
    return None


def _all_text_this_call_would_write(tool_input: dict) -> str:
    parts = [str(tool_input[key]) for key in ("content", "new_string") if key in tool_input]
    parts += [
        str(edit["new_string"])
        for edit in tool_input.get("edits") or []
        if isinstance(edit, dict) and "new_string" in edit
    ]
    return "\n".join(parts)


def _violation(payload: dict) -> str | None:
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}

    if tool == "Bash":
        command = str(tool_input.get("command", ""))
        return _dangerous_shell(command) or _dangerous_git(command)
    if tool in ("Write", "Edit", "MultiEdit"):
        return _dangerous_write(
            str(tool_input.get("file_path", "")), _all_text_this_call_would_write(tool_input)
        )
    return None


def _allow_by_failing_open() -> None:
    return


def main() -> None:
    try:
        reason = _violation(json.load(sys.stdin))
    except Exception:
        return _allow_by_failing_open()

    if reason:
        _deny_and_exit(reason)


if __name__ == "__main__":
    main()
