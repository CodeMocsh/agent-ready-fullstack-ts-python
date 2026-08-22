import { defineConfig, devices } from "@playwright/test";

// Live mode runs against the dev server, so the app is served from "/" and the
// backend half answers through its /api proxy. The subpath rigour lives in
// playwright.config.ts, which is the one that runs by default; this config exists
// to look at the real system, and expects `make dev` to be running already.
//
// It therefore reads the same variable `make dev` does. This config starts no
// server, so a hard-coded port does not fail when it is wrong -- it points at
// whatever else is listening, which in a second checkout is the first checkout's
// app, and the run then reports on code nobody changed.
const FRONTEND_PORT = Number(process.env.FRONTEND_PORT ?? 5173);

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.live.spec.ts",
  fullyParallel: false,
  reporter: "list",
  use: {
    baseURL: `http://localhost:${FRONTEND_PORT}/`,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
