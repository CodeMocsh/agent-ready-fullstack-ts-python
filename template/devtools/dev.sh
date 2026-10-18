#!/bin/sh
set -eu

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
export BACKEND_PORT FRONTEND_PORT
export APP_ENV=development
export PYTHONASYNCIODEBUG=1

set -m

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
    kill "-$backend_pid" 2>/dev/null || true
    sleep 1
    kill -KILL "-$backend_pid" 2>/dev/null || true
    wait "$backend_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "==> backend  http://localhost:$BACKEND_PORT"
(cd "$here/backend" && own_group uv run uvicorn app.main:app --reload --port "$BACKEND_PORT") &
backend_pid=$!

deadline=$(($(date +%s) + 30))
until curl -fsS --max-time 2 "http://localhost:$BACKEND_PORT/health" >/dev/null 2>&1; do
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
cd "$here/frontend"
pnpm dev:live
