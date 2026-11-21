import { QueryObserver } from "@tanstack/react-query";
import { createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { afterEach, beforeEach, vi } from "vitest";
import { request } from "@/api/client";
import { createQueryClient, followNavigations } from "@/query-client";

function fresh() {
  return {
    version: 0,
    active: 0,
    peak: 0,
    latency: 150,
    fail: false,
    networkFailures: 0,
    starts: [] as number[],
  };
}

export const server = fresh();
export const stops: Array<() => void> = [];
const clients: ReturnType<typeof createQueryClient>[] = [];

export function client(record = vi.fn()) {
  const value = createQueryClient(record);
  clients.push(value);
  stops.push(
    followNavigations(
      createRouter({
        routeTree: createRootRoute(),
        history: createMemoryHistory(),
      }),
      value,
    ),
  );
  return value;
}

export function watch(value: ReturnType<typeof client>, key = "tasks") {
  const observer = new QueryObserver(value, {
    queryKey: [key],
    queryFn: ({ signal }) => request<number>(`/${key}`, { signal }),
    retry: false,
  });
  const stop = observer.subscribe(() => undefined);
  stops.push(stop);
  return { observer, stop };
}

export function mutateTasks(value: ReturnType<typeof client>): Promise<unknown> {
  window.dispatchEvent(new Event("click"));
  return value
    .getMutationCache()
    .build(value, { mutationFn: async () => undefined, meta: { invalidates: [["tasks"]] } })
    .execute(undefined);
}

async function answer(init: RequestInit | undefined): Promise<Response> {
  const snapshot = server.version;
  const failure = server.fail;
  server.starts.push(performance.now());
  server.active += 1;
  server.peak = Math.max(server.peak, server.active);
  try {
    if (server.networkFailures > 0) {
      server.networkFailures -= 1;
      throw new TypeError("Network unavailable");
    }
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(resolve, server.latency);
      init?.signal?.addEventListener(
        "abort",
        () => {
          clearTimeout(timer);
          reject(init.signal?.reason);
        },
        { once: true },
      );
    });
    return new Response(failure ? '{"detail":"failed"}' : JSON.stringify(snapshot), {
      status: failure ? 500 : 200,
    });
  } finally {
    server.active -= 1;
  }
}

export function serveEventReads(): void {
  beforeEach(() => {
    vi.useFakeTimers();
    Object.assign(server, fresh());
    vi.spyOn(globalThis, "fetch").mockImplementation((_url, init) => answer(init));
  });

  afterEach(async () => {
    for (const stop of stops.splice(0).reverse()) stop();
    for (const value of clients.splice(0)) value.clear();
    await vi.advanceTimersByTimeAsync(1);
    vi.restoreAllMocks();
    vi.useRealTimers();
  });
}
