#!/bin/sh
# Run the contract suite against the backend half.
#
# The same file has already run against the mock handlers under `pnpm test`. This
# run points it at the dev server instead, so one pass covers the proxy, the prefix
# rewrite, real HTTP, and the real store -- the things no amount of per-half testing
# can see. Both halves are started here and stopped on the way out.
set -eu

# Job control, so each half becomes its own process group and can be killed as one.
# `uv run` and `pnpm` both exec through a launcher that spawns the real server as a
# child: killing the pid we started leaves that child holding the port, and the next
# run dies on --strictPort.
set -m

here="$(cd "$(dirname "$0")/.." && pwd)"
base_url="${CONTRACT_BASE_URL:-http://localhost:5173/api}"
backend_pid=""
frontend_pid=""
log="$(mktemp)"

cleanup() {
    for pid in $frontend_pid $backend_pid; do
        kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    done
    rm -f "$log"
}
trap cleanup EXIT INT TERM

fail() {
    echo "contract: $1" >&2
    echo "--- server output ---" >&2
    cat "$log" >&2
    exit 1
}

(cd "$here/backend" && exec uv run uvicorn app.main:app --port 8000 --log-level warning) \
    >>"$log" 2>&1 &
backend_pid=$!

(cd "$here/frontend" && exec pnpm dev:live --port 5173 --strictPort) >>"$log" 2>&1 &
frontend_pid=$!

# `--max-time` bounds a probe that can otherwise wait forever: a half that is
# listening but never replies holds the connection open, and an unbounded curl
# hangs there rather than reaching the deadline below.
deadline=$(($(date +%s) + 60))
until curl -fsS --max-time 2 "$base_url/tasks" >/dev/null 2>&1; do
    kill -0 "$backend_pid" 2>/dev/null || fail "the backend exited during startup"
    kill -0 "$frontend_pid" 2>/dev/null || fail "the dev server exited during startup"
    [ "$(date +%s)" -ge "$deadline" ] && fail "$base_url/tasks did not answer within 60s"
    sleep 0.5
done

echo "==> both halves up; $base_url reachable"
cd "$here/frontend"
CONTRACT_TARGET=live VITE_API_BASE_URL="$base_url" pnpm exec vitest run tests/contract.test.ts
