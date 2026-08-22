import { defineConfig, devices } from "@playwright/test";

// From the environment for the same reason BACKEND_PORT and FRONTEND_PORT are: a
// second checkout is a normal way to work, and this run binds a port of its own.
const PORT = Number(process.env.PREVIEW_PORT ?? 4173);

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
    // Never reuse a server this run did not start. Playwright only checks that
    // *something* answers on the URL, not that it is this build, so any other app
    // holding the port is indistinguishable from the real one and the whole suite
    // runs against it -- reporting a pass or a failure that is about someone
    // else's code. That is the worst shape a test can take, and it is not
    // hypothetical: a preview server from a second checkout absorbed this suite
    // and the specs failed against an unrelated app. Refusing costs a rebuild of
    // a few seconds and turns a wrong answer into a named port conflict; run two
    // at once with PREVIEW_PORT.
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
