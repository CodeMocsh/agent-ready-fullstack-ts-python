#!/bin/sh
# Run both halves: the backend on :8000 and the frontend on :5173 in live mode, so
# requests go through the dev server's /api proxy to the backend rather than to the
# mock handlers. `make dev-frontend` is the mock-mode loop, which needs no backend.
#
# Child output is redirected rather than inherited on purpose: a backgrounded server
# holding this script's stdout open keeps a pipe from ever seeing EOF, so
# `make dev | tee log` would hang after the script itself exits.
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
# like frontend bugs.
waited=0
until curl -fsS http://localhost:8000/tasks >/dev/null 2>&1; do
    if ! kill -0 "$backend_pid" 2>/dev/null; then
        echo "dev: the backend exited during startup; run 'make dev-backend' to see why" >&2
        exit 1
    fi
    waited=$((waited + 1))
    if [ "$waited" -gt 60 ]; then
        echo "dev: the backend did not answer on :8000 within 30s" >&2
        exit 1
    fi
    sleep 0.5
done

echo "==> frontend http://localhost:5173 (live mode -- mock handlers off)"
cd "$here/frontend" && exec pnpm dev:live
