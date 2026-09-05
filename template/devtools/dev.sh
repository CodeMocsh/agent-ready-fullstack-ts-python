#!/bin/sh
# Run both halves: the backend and the frontend in live mode, so
# requests go through the dev server's /api proxy to the backend rather than to the
# mock handlers. `make dev-frontend` is the mock-mode loop, which needs no backend.
set -eu

# Both ports are overridable so two checkouts can run at once. vite.config.ts reads the
# same two variables, which is what keeps the proxy pointed at the backend this script
# actually started -- they used to be hard-coded in both files and had to be kept in
# agreement by hand.
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
export BACKEND_PORT FRONTEND_PORT

# Job control, so vite runs as a foreground job of its own and keeps the terminal. It is
# not what groups the backend: dash turns job control off when it has no controlling
# terminal, so `set -m` alone leaves a `make dev` that was not started from a terminal
# with no group to kill. own_group below is what the backend's group comes from.
set -m

# The backend in a process group of its own, so one kill stops all of it. `uv run` execs
# a launcher that spawns uvicorn as a child, and killing the pid this script holds
# reaches the launcher and leaves uvicorn on the port. setsid needs no terminal, and it
# makes the leader the pid this script already holds.
# setsid refuses with EPERM when the caller is already a process group leader, which is
# what `set -m` above has just made this subshell. That is the state being asked for, so
# the refusal is not a failure -- but only the assertion after it says so. Without that,
# the except would be the swallow that hands back an ungrouped process.
own_group() {
    exec python3 -c 'import os, sys
try:
    os.setsid()
except PermissionError:
    pass
if os.getpgrp() != os.getpid():
    raise SystemExit("own_group: not a process group leader, so a kill cannot stop this")
os.execvp(sys.argv[1], sys.argv[1:])' "$@"
}

here="$(cd "$(dirname "$0")/.." && pwd)"
backend_pid=""

cleanup() {
    [ -n "$backend_pid" ] || return 0
    # Ask, wait a moment, then insist. uvicorn's --reload supervisor does not always
    # exit once its worker is gone, and `uv run` waits for it: a lone SIGTERM leaves
    # this blocked in `wait` forever, so Ctrl-C never gives the terminal back. The
    # escalation is unconditional because polling for "are you gone yet" cannot tell
    # a live process from an unreaped one. A dev server has nothing to flush, and a
    # second is generous.
    #
    # The group, and only the group, and no `--` before it: dash's kill builtin refuses
    # the separator and sends nothing at all. A single-pid fallback behind either kill
    # reaches the launcher and leaves uvicorn, which is the bug both spellings hid.
    kill "-$backend_pid" 2>/dev/null || true
    sleep 1
    kill -KILL "-$backend_pid" 2>/dev/null || true
    wait "$backend_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "==> backend  http://localhost:$BACKEND_PORT"
(cd "$here/backend" && own_group uv run uvicorn app.main:app --reload --port "$BACKEND_PORT") &
backend_pid=$!

# Fail loudly here rather than letting vite start and serve proxy errors that look
# like frontend bugs. `--max-time` is what makes the loop below reachable: when the
# app fails to import, uvicorn's --reload parent stays up holding the listening
# socket while the worker crash-loops, so an unbounded curl waits for a reply that
# is never written and neither exit below is ever taken.
deadline=$(($(date +%s) + 30))
until curl -fsS --max-time 2 "http://localhost:$BACKEND_PORT/tasks" >/dev/null 2>&1; do
    if ! kill -0 "$backend_pid" 2>/dev/null; then
        echo "dev: the backend exited during startup; run 'make dev-backend' to see why" >&2
        exit 1
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
        echo "dev: the backend did not answer on :$BACKEND_PORT within 30s; the output above says why" >&2
        exit 1
    fi
    sleep 0.5
done

echo "==> frontend http://localhost:$FRONTEND_PORT (live mode -- mock handlers off)"
# Run the frontend in the foreground without `exec`, so this shell outlives it and
# the cleanup trap still fires. `exec` replaces the shell and discards the trap, and
# job control has already put the backend in a process group of its own, so a Ctrl-C
# in the terminal reaches neither -- uvicorn keeps the backend port after vite is gone.
#
# Foreground also means vite keeps the terminal, so its keyboard shortcuts work. The
# price is that a signal sent to this script alone cannot be handled until vite
# returns; Ctrl-C is fine, because the terminal delivers it to vite's group directly.
# `check_template.sh` drives a pty for exactly that reason.
cd "$here/frontend"
pnpm dev:live
