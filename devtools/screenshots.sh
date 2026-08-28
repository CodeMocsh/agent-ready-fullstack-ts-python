#!/bin/sh
# Regenerate the README's screenshot from a project this script renders, so the picture is
# of the current template rather than of whatever it looked like the day somebody dragged a
# PNG in. Mock mode only: live mode is the same screen with one line of text different, and
# the half that proves the two halves talk is the contract suite, not a picture.
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

for tool in git python3 node pnpm; do
    command -v "$tool" >/dev/null 2>&1 || fail "$tool is required and not on PATH."
done

# The same allocator check_template.sh uses, for the same reason: a second checkout is a
# normal way to work, and a shot taken against somebody else's preview server is a picture
# of the wrong program that looks exactly right.
free_port() {
    python3 -c 'import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()'
}
PREVIEW_PORT="$(free_port)"
export PREVIEW_PORT

PROJECT="$(sh "$RENDER" | tail -n1)"
[ -d "$PROJECT" ] || fail "render.sh printed no project directory."
cd "$PROJECT"
echo "==> rendered $PROJECT"

echo "==> install the frontend half"
pnpm -C frontend install >/dev/null
pnpm -C frontend exec playwright install chromium >/dev/null

cat >frontend/e2e/screenshots.spec.ts <<'SPEC'
import { test, expect } from "@playwright/test";

test.use({ viewport: { width: 1280, height: 720 } });

test("screenshot", async ({ page }) => {
  await page.goto("./");
  await expect(page.getByRole("heading", { name: "Tasks" })).toBeVisible();
  await expect(page.getByRole("table")).toBeVisible();
  await page.screenshot({ path: `${process.env.SHOT_DIR}/mock.png` });
});
SPEC

mkdir -p "$OUT_DIR"
export SHOT_DIR="$OUT_DIR"

echo "==> mock mode: no backend, no Python"
pnpm -C frontend exec playwright test e2e/screenshots.spec.ts >/dev/null

[ -s "$OUT_DIR/mock.png" ] || fail "$OUT_DIR/mock.png was not written."
echo "    wrote docs/img/mock.png"
echo "==> OK. The render is yours to remove: $PROJECT"
