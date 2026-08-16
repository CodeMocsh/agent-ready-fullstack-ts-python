import { defineConfig, devices } from "@playwright/test";

const PORT = 4173;

// Served from a subdirectory rather than the domain root, because plenty of
// hosts do that and everything which only works at "/" fails here: the service
// worker's registration URL and therefore its scope, the API paths it can
// intercept, the router's basepath. All of them pass at the root, which is what
// makes the root the weaker test.
const BASE_PATH = "/preview/";

export default defineConfig({
  testDir: "./e2e",
  // The live-mode spec needs both halves running and has its own config.
  testIgnore: "**/*.live.spec.ts",
  fullyParallel: true,
  reporter: "list",
  use: {
    baseURL: `http://localhost:${PORT}${BASE_PATH}`,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    // --base is needed on both: build stamps it into the asset URLs, preview
    // decides which path those assets are actually served from.
    command:
      `pnpm build:mock --base="${BASE_PATH}" && ` +
      `pnpm preview --base="${BASE_PATH}" --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}${BASE_PATH}`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
