#!/bin/sh
set -eu

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
export APP_ENV=development
set -a
. "$here/devtools/contract-test.env"
set +a
base_url="${CONTRACT_BASE_URL:-http://localhost:$FRONTEND_PORT/api}"
backend_pid=""
frontend_pid=""
worker_pid=""
log="$(mktemp)"

signal_halves() {
    for pid in $frontend_pid $worker_pid $backend_pid; do
        kill -"$1" "-$pid" 2>/dev/null || true
    done
}

say_what_admission_would_refuse() {
    [ "${ADMISSION_MODE:-}" = evaluate ] || return 0
    python3 - "$log" <<'PY'
import collections
import json
import sys

counted = collections.Counter()
with open(sys.argv[1]) as lines:
    for line in lines:
        if line.startswith("{"):
            record = json.loads(line)
            if record.get("message") == "admission would refuse":
                counted[record["bound"]] += 1
said = ", ".join(f"{bound} {count}" for bound, count in sorted(counted.items()))
if not counted:
    said = "nothing"
print(f"contract: admission would refuse: {said}")
PY
}

cleanup() {
    say_what_admission_would_refuse || true
    signal_halves TERM
    sleep 1
    signal_halves KILL
    for pid in $frontend_pid $worker_pid $backend_pid; do
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

(cd "$here/backend" && own_group uv run uvicorn app.main:app --port "$BACKEND_PORT" --log-level warning) \
    >>"$log" 2>&1 &
backend_pid=$!

(cd "$here/frontend" && own_group pnpm dev:live --port "$FRONTEND_PORT" --strictPort) >>"$log" 2>&1 &
frontend_pid=$!

if [ -n "${DATABASE_URL:-}" ]; then
    (cd "$here/backend" && own_group uv run python -m app.worker) >>"$log" 2>&1 &
    worker_pid=$!
fi

deadline=$(($(date +%s) + 60))
until curl -fsS --max-time 2 "$base_url/health" >/dev/null 2>&1; do
    kill -0 "$backend_pid" 2>/dev/null || fail "the backend exited during startup"
    kill -0 "$frontend_pid" 2>/dev/null || fail "the dev server exited during startup"
    if [ -n "$worker_pid" ] && ! kill -0 "$worker_pid" 2>/dev/null; then
        fail "the worker exited during startup"
    fi
    [ "$(date +%s)" -ge "$deadline" ] && fail "$base_url/health did not answer within 60s"
    sleep 0.5
done

echo "==> both halves up; $base_url reachable${worker_pid:+; a worker beside the backend}"
cd "$here/frontend"
CONTRACT_TARGET=live VITE_API_BASE_URL="$base_url" pnpm exec vitest run contract.test
