import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";
import { server } from "@/mocks/node";
import { taskStore } from "@/mocks/store";

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
    taskStore.reset();
  }
});

afterAll(() => {
  if (!againstLiveBackend) {
    server.close();
  }
});
