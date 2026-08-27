#!/bin/sh
# Regenerate the README's screenshots from a project this script renders, so the
# picture is of the current template rather than of whatever it looked like the day
# somebody dragged a PNG in.
#
# Not in the gate, deliberately. check_template.sh downloads no browser and runs no
# playwright -- backend/tests/tiers.py argues why -- and a screenshot is a picture
# rather than an assertion, so nothing here can fail in a way a check would catch.
# Run it when the screen changes.
#
# Usage: devtools/screenshots.sh
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
RENDER="$REPO/devtools/render.sh"
OUT_DIR="$REPO/docs/img"

fail() { echo "screenshots: $*" >&2; exit 1; }

for tool in uv git python3 node pnpm curl; do
    command -v "$tool" >/dev/null 2>&1 || fail "$tool is required and not on PATH."
done

# The same allocator check_template.sh uses, for the same reason: a second checkout
# is a normal way to work, and a live-mode shot taken against somebody else's app on
# :5173 is a picture of the wrong program that looks exactly right.
free_port() {
    python3 -c 'import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()'
}
BACKEND_PORT="$(free_port)"
FRONTEND_PORT="$(free_port)"
PREVIEW_PORT="$(free_port)"
export BACKEND_PORT FRONTEND_PORT PREVIEW_PORT

PROJECT="$(sh "$RENDER" | tail -n1)"
[ -d "$PROJECT" ] || fail "render.sh printed no project directory."
cd "$PROJECT"
echo "==> rendered $PROJECT"

echo "==> install the frontend half"
pnpm -C frontend install >/dev/null
pnpm -C frontend exec playwright install chromium >/dev/null

# Two files with one body. The configs select by filename -- the mock one ignores
# **/*.live.spec.ts and the live one matches only that -- so a single spec would be
# invisible to whichever run it was not named for.
cat >frontend/e2e/screenshots.spec.ts <<'SPEC'
import { test, expect } from "@playwright/test";

const MODE = process.env.SHOT_MODE ?? "mock";

test.use({ viewport: { width: 1280, height: 720 } });

test(`screenshot ${MODE}`, async ({ page }) => {
  await page.goto("./");
  await expect(page.getByRole("heading", { name: "Tasks" })).toBeVisible();
  await expect(page.getByRole("table")).toBeVisible();
  await page.screenshot({ path: `${process.env.SHOT_DIR}/${MODE}.png` });
});
SPEC
cp frontend/e2e/screenshots.spec.ts frontend/e2e/screenshots.live.spec.ts

mkdir -p "$OUT_DIR"
export SHOT_DIR="$OUT_DIR"

echo "==> mock mode: no backend, no Python"
SHOT_MODE=mock pnpm -C frontend exec playwright test e2e/screenshots.spec.ts >/dev/null

echo "==> install the backend half"
(cd backend && uv sync --all-groups >/dev/null 2>&1) || fail "the backend half would not install."

echo "==> live mode: both halves, through the dev proxy"
# Under a pty, and stopped with Ctrl-C, because that is the only faithful way and a
# plain kill deadlocks. dev.sh keeps vite in the foreground, so a signal sent to the
# script alone cannot be handled until vite returns -- and `uv run uvicorn --reload`
# spawns a child that survives a kill aimed at the launcher, which leaves the backend
# holding a port after this exits. check_template.sh stops `make dev` the same way,
# for the same two reasons.
python3 - "$PROJECT" <<'PYEOF'
import os
import pty
import signal
import subprocess
import sys
import time

project = sys.argv[1]
FRONTEND_PORT = os.environ["FRONTEND_PORT"]
BACKEND_PORT = os.environ["BACKEND_PORT"]
PROXY = f"http://localhost:{FRONTEND_PORT}/api/tasks"
BACKEND = f"http://localhost:{BACKEND_PORT}/tasks"


def serving(url):
    return subprocess.run(
        ["curl", "-fsS", "--max-time", "2", url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


if serving(PROXY):
    print(f"screenshots: :{FRONTEND_PORT} is already answering, so the shot would be of "
          "whatever is there rather than of this render.", file=sys.stderr)
    raise SystemExit(1)

pid, master = pty.fork()
if pid == 0:
    os.chdir(project)
    os.execvp("sh", ["sh", "devtools/dev.sh"])

os.set_blocking(master, False)
output = bytearray()


def drain():
    # The child writes into a pty buffer of fixed size, so a reader that stops reading
    # wedges the very process it is driving once that buffer fills.
    while True:
        try:
            chunk = os.read(master, 65536)
        except (BlockingIOError, OSError):
            return
        if not chunk:
            return
        output.extend(chunk)


def until(predicate, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        drain()
        if predicate():
            return True
        time.sleep(0.5)
    return False


def die(message):
    drain()
    print(f"screenshots: {message}", file=sys.stderr)
    print("--- dev.sh output ---", file=sys.stderr)
    sys.stderr.write(bytes(output).decode("utf-8", "replace"))
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except OSError:
        pass
    raise SystemExit(1)


if not until(lambda: serving(PROXY), 120):
    die(f"dev.sh did not serve :{FRONTEND_PORT}/api within 120s")

shot = subprocess.Popen(
    ["pnpm", "-C", "frontend", "exec", "playwright", "test",
     "--config", "playwright.live.config.ts", "e2e/screenshots.live.spec.ts"],
    cwd=project,
    env={**os.environ, "SHOT_MODE": "live"},
    stdout=subprocess.DEVNULL,
)
# Polled rather than waited on, so the pty keeps draining while playwright runs.
if not until(lambda: shot.poll() is not None, 300):
    shot.kill()
    die("the live-mode screenshot did not finish within 300s")
failed = shot.returncode != 0

os.write(master, b"\x03")
if not until(lambda: os.waitpid(pid, os.WNOHANG)[0] == pid, 30):
    die("dev.sh was still running 30s after Ctrl-C")
if not until(lambda: not serving(BACKEND) and not serving(PROXY), 15):
    die("dev.sh exited but a port is still answering, so this run leaked a server")

if failed:
    print("screenshots: the live-mode shot failed, so the two halves did not talk.",
          file=sys.stderr)
    raise SystemExit(1)
PYEOF

for shot in mock live; do
    [ -s "$OUT_DIR/$shot.png" ] || fail "$OUT_DIR/$shot.png was not written."
    echo "    wrote docs/img/$shot.png"
done
echo "==> OK. The render is yours to remove: $PROJECT"
