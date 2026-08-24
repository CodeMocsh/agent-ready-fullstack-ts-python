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

# A free pair, found rather than fixed. This throwaway pair is nobody's dev server and
# the numbers matter to nothing outside this script, so defaulting to 8000 and 5173 only
# made the gate fail whenever another checkout of this project was already running one --
# and the gate is the one thing that must never be circumstantially red. Still overridable,
# because a caller that has to know the ports in advance is the case CONTRACT_BASE_URL serves.
#
# Both sockets stay bound until the interpreter exits, so the two cannot come back as the
# same number. Asking twice in two processes is the obvious spelling and it is wrong: each
# closes its socket before the next one binds, which leaves the kernel free to hand the same
# port to both -- rare, load-dependent, and indistinguishable from a flaky gate.
PORTS="$(python3 - <<'PY'
import socket

held = []
for _ in range(2):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    held.append(sock)
print(" ".join(str(sock.getsockname()[1]) for sock in held))
for sock in held:
    sock.close()
PY
)"
BACKEND_PORT="${BACKEND_PORT:-${PORTS%% *}}"
FRONTEND_PORT="${FRONTEND_PORT:-${PORTS##* }}"
export BACKEND_PORT FRONTEND_PORT
base_url="${CONTRACT_BASE_URL:-http://localhost:$FRONTEND_PORT/api}"
backend_pid=""
frontend_pid=""
log="$(mktemp)"

signal_halves() {
    for pid in $frontend_pid $backend_pid; do
        kill -"$1" -- -"$pid" 2>/dev/null || kill -"$1" "$pid" 2>/dev/null || true
    done
}

cleanup() {
    # Ask, wait a moment, then insist. A launcher that does not exit after its child
    # is gone would leave this blocked in `wait` forever, holding the frontend port -- and the
    # next run dies on --strictPort, in CI, for a reason that is nowhere in the log.
    signal_halves TERM
    sleep 1
    signal_halves KILL
    for pid in $frontend_pid $backend_pid; do
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

(cd "$here/backend" && exec uv run uvicorn app.main:app --port "$BACKEND_PORT" --log-level warning) \
    >>"$log" 2>&1 &
backend_pid=$!

(cd "$here/frontend" && exec pnpm dev:live --port "$FRONTEND_PORT" --strictPort) >>"$log" 2>&1 &
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
CONTRACT_TARGET=live VITE_API_BASE_URL="$base_url" pnpm exec vitest run tests/api/contract.test.ts
