import { defineConfig, devices } from "@playwright/test";

// Live mode runs against the dev server, so the app is served from "/" and the
// backend half answers through its /api proxy. The subpath rigour lives in
// playwright.config.ts, which is the one that runs by default; this config exists
// to look at the real system, and expects `make dev` to be running already.
export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.live.spec.ts",
  fullyParallel: false,
  reporter: "list",
  use: {
    baseURL: "http://localhost:5173/",
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
