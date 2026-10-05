import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";
import { resetMockState } from "@/mocks/handlers";
import { server } from "@/mocks/node";

const againstLiveBackend = process.env.CONTRACT_TARGET === "live";

beforeAll(() => {
  if (!againstLiveBackend) {
    server.listen({ onUnhandledRequest: "error" });
  }
});

afterEach(() => {
  cleanup();
  if (!againstLiveBackend) {
    server.resetHandlers();
    resetMockState();
  }
});

afterAll(() => {
  if (!againstLiveBackend) {
    server.close();
  }
});
