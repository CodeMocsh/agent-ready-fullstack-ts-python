import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";
import { server } from "@/mocks/node";
import { taskStore } from "@/mocks/store";

// The contract suite runs twice: once against these handlers and once against the
// backend half. Starting the worker in the live run would intercept the requests
// that run is there to make, and the suite would pass while proving nothing.
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
