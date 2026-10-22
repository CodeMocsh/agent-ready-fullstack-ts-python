import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, expect, vi } from "vitest";
import { resetMockState } from "@/mocks/handlers";
import { server } from "@/mocks/node";
import { releaseEveryHeldRequest } from "./held.ts";

const againstLiveBackend = process.env.CONTRACT_TARGET === "live";

const PATIENCE_MS = 5000;

configure({ asyncUtilTimeout: PATIENCE_MS });

const inFlight = new Map<string, string>();

function settled({ requestId }: { requestId: string }): void {
  inFlight.delete(requestId);
}

beforeAll(() => {
  if (!againstLiveBackend) {
    server.events.on("request:start", ({ request, requestId }) => {
      inFlight.set(requestId, `${request.method} ${request.url}`);
    });
    server.events.on("request:end", settled);
    server.events.on("request:unhandled", settled);
    server.events.on("unhandledException", settled);
    server.listen({ onUnhandledRequest: "error" });
  }
});

afterEach(async () => {
  cleanup();
  if (!againstLiveBackend) {
    releaseEveryHeldRequest();
    try {
      await vi.waitFor(
        () =>
          expect(
            [...inFlight.values()],
            "a request this test started is still unanswered; hold one on purpose with heldUntilTheTestEnds",
          ).toEqual([]),
        { timeout: PATIENCE_MS },
      );
    } finally {
      inFlight.clear();
      server.resetHandlers();
      resetMockState();
    }
  }
});

afterAll(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  localStorage.clear();
  sessionStorage.clear();
  window.history.replaceState(null, "", "/");
  if (!againstLiveBackend) {
    server.close();
    server.events.removeAllListeners();
  }
});
