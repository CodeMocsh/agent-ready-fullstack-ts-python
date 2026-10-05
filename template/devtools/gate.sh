#!/bin/sh
# The gate, before the commit. `make gate` is the list of checks; this script decides whether
# this clone can run it, queues it behind any other gate on the machine, and skips a tree that
# already passed. `make pre-commit` runs it, and the git hook runs `make pre-commit`.
# docs/adr/0015.
#
# A half that is not installed is skipped rather than failing the commit, because a clone that
# has installed only one of them is a legitimate way to work here. A run where everything
# skipped exits 0 and looks like a run where everything passed, so a partial run says what it
# did not do, on stderr, every time. The workflow runs `make gate` itself, so nothing skips
# there.
set -eu

cd "$(git rev-parse --show-toplevel)"

# git runs the hook with GIT_DIR and GIT_INDEX_FILE pointing at the commit being made, and
# every `git` a test starts inherits them. The gate reads the repository the ordinary way.
unset $(git rev-parse --local-env-vars)

has_frontend=0
has_backend=0
if command -v pnpm >/dev/null 2>&1 && [ -d frontend/node_modules ]; then
    has_frontend=1
fi
if command -v uv >/dev/null 2>&1 && [ -d backend/.venv ]; then
    has_backend=1
fi
has_both=$((has_frontend * has_backend))

if [ "$has_both" = 1 ]; then
    passed="$(git rev-parse --git-path gate-passed)"
    before="$(sh devtools/worktree-tree.sh)"
    if [ -f "$passed" ] && [ "$(cat "$passed")" = "$before" ]; then
        echo "pre-commit: tree $before already passed the gate in this checkout; not running it again" >&2
        exit 0
    fi
fi

# One gate per machine, whichever project or worktree started it. Two gates at once
# oversubscribe every core twice, and each then fails the other's timeouts.
lock="/tmp/pre-commit-gate-$(id -u).lock"
exec 9>>"$lock"
perl devtools/hold-the-gate.pl "$lock"

if [ "$has_both" = 1 ]; then
    make -s gate 9>&-
    if [ "$(sh devtools/worktree-tree.sh)" = "$before" ]; then
        echo "$before" >"$passed"
    fi
    exit 0
fi

# Below here the gate cannot run as one, because two of its members -- the contract suite and
# the artifact check -- are about the two halves agreeing and have nothing to compare against.
# What is left runs directly.
failed=0

if [ "$has_frontend" = 1 ]; then
    pnpm -C frontend lint:check 9>&- || failed=1
    pnpm -C frontend test 9>&- || failed=1
fi

if [ "$has_backend" = 1 ]; then
    (cd backend && uv run --no-sync python devtools/lint.py --check) 9>&- || failed=1
    (cd backend && uv run --no-sync pytest -q) 9>&- || failed=1
fi

echo "" >&2
echo "pre-commit: PARTIAL RUN -- this commit was not checked by the whole gate." >&2
[ "$has_frontend" = 1 ] || echo "  frontend        SKIPPED (no pnpm, or run 'make install')" >&2
[ "$has_backend" = 1 ] || echo "  backend         SKIPPED (no uv, or run 'make install')" >&2
echo "  openapi-check   SKIPPED (needs both halves)" >&2
echo "  contract suite  SKIPPED (needs both halves)" >&2
echo "Run 'make install', then 'make pre-commit', to check it properly." >&2

exit "$failed"
