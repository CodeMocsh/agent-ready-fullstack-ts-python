#!/bin/sh
# Run both halves: the backend on :8000 and the frontend on :5173 in live mode, so
# requests go through the dev server's /api proxy to the backend rather than to the
# mock handlers. `make dev-frontend` is the mock-mode loop, which needs no backend.
set -eu

# Job control, so the backend becomes its own process group. `uv run` execs a
# launcher that spawns uvicorn as a child, and killing only the pid we started
# leaves that child holding :8000 after this script exits.
set -m

here="$(cd "$(dirname "$0")/.." && pwd)"
backend_pid=""

cleanup() {
    if [ -n "$backend_pid" ]; then
        kill -- -"$backend_pid" 2>/dev/null || kill "$backend_pid" 2>/dev/null || true
        wait "$backend_pid" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

echo "==> backend  http://localhost:8000"
(cd "$here/backend" && exec uv run uvicorn app.main:app --reload --port 8000) &
backend_pid=$!

# Fail loudly here rather than letting vite start and serve proxy errors that look
# like frontend bugs. `--max-time` is what makes the loop below reachable: when the
# app fails to import, uvicorn's --reload parent stays up holding the listening
# socket while the worker crash-loops, so an unbounded curl waits for a reply that
# is never written and neither exit below is ever taken.
deadline=$(($(date +%s) + 30))
until curl -fsS --max-time 2 http://localhost:8000/tasks >/dev/null 2>&1; do
    if ! kill -0 "$backend_pid" 2>/dev/null; then
        echo "dev: the backend exited during startup; run 'make dev-backend' to see why" >&2
        exit 1
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
        echo "dev: the backend did not answer on :8000 within 30s; the output above says why" >&2
        exit 1
    fi
    sleep 0.5
done

echo "==> frontend http://localhost:5173 (live mode -- mock handlers off)"
# Run the frontend in the foreground without `exec`, so this shell outlives it and
# the cleanup trap still fires. `exec` replaces the shell and discards the trap, and
# job control has already put the backend in a process group of its own, so a Ctrl-C
# in the terminal reaches neither -- uvicorn keeps :8000 after vite is gone.
cd "$here/frontend"
pnpm dev:live
