import { InfiniteQueryObserver, QueryObserver } from "@tanstack/react-query";
import { createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { request } from "@/api/client";
import { createQueryClient, followNavigations, invalidateForServerEvent } from "@/query-client";

let version = 0;
let active = 0;
let peak = 0;
let latency = 150;
let fail = false;
let networkFailures = 0;
const starts: number[] = [];
const clients: ReturnType<typeof createQueryClient>[] = [];
const stops: Array<() => void> = [];

function client(record = vi.fn()) {
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

function watch(value: ReturnType<typeof client>, key = "tasks") {
  const observer = new QueryObserver(value, {
    queryKey: [key],
    queryFn: ({ signal }) => request<number>(`/${key}`, { signal }),
    retry: false,
  });
  const stop = observer.subscribe(() => undefined);
  stops.push(stop);
  return { observer, stop };
}

beforeEach(() => {
  vi.useFakeTimers();
  version = 0;
  active = 0;
  peak = 0;
  latency = 150;
  fail = false;
  networkFailures = 0;
  starts.length = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (_url, init) => {
    const snapshot = version;
    const failure = fail;
    starts.push(performance.now());
    active += 1;
    peak = Math.max(peak, active);
    try {
      if (networkFailures > 0) {
        networkFailures -= 1;
        throw new TypeError("Network unavailable");
      }
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(resolve, latency);
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
      active -= 1;
    }
  });
});

afterEach(async () => {
  for (const stop of stops.splice(0).reverse()) stop();
  for (const value of clients.splice(0)) value.clear();
  await vi.advanceTimersByTimeAsync(1);
  vi.restoreAllMocks();
  vi.useRealTimers();
});

it("merges separate event tasks and reads the final state without overlapping transport", async () => {
  const value = client();
  const { observer } = watch(value);
  await vi.advanceTimersByTimeAsync(160);
  const changes: Promise<void>[] = [];
  for (version = 1; version <= 80; version += 1) {
    changes.push(invalidateForServerEvent(value, [["tasks"], ["tasks"]]));
    await vi.advanceTimersByTimeAsync(5);
  }
  version = 80;
  await vi.advanceTimersByTimeAsync(1000);
  await Promise.all(changes);
  expect(starts).toHaveLength(3);
  expect(peak).toBe(1);
  expect(observer.getCurrentResult().data).toBe(80);
  await observer.refetch();
  expect(observer.getCurrentResult().error?.name).toBe("CycleStopped");
});

it("preserves changes during the initial read and during the trailing read", async () => {
  const value = client();
  const { observer } = watch(value);
  await vi.advanceTimersByTimeAsync(10);
  version = 1;
  const first = invalidateForServerEvent(value, [["tasks"]]);
  await vi.advanceTimersByTimeAsync(155);
  version = 2;
  const last = invalidateForServerEvent(value, [["tasks"]]);
  await vi.advanceTimersByTimeAsync(1000);
  await Promise.all([first, last]);
  expect(observer.getCurrentResult().data).toBe(2);
  expect(peak).toBe(1);
  expect(starts).toHaveLength(3);
});

it("leaves inactive data stale and spends only one event cause on its next mount", async () => {
  const value = client();
  const first = watch(value);
  await vi.advanceTimersByTimeAsync(160);
  first.stop();
  version = 1;
  await Promise.all(Array.from({ length: 80 }, () => invalidateForServerEvent(value, [["tasks"]])));
  expect(starts).toHaveLength(1);
  const next = watch(value);
  await vi.advanceTimersByTimeAsync(660);
  expect(next.observer.getCurrentResult().data).toBe(1);
  await next.observer.refetch();
  expect(next.observer.getCurrentResult().error?.name).toBe("CycleStopped");
});

it("reports a failed refresh and reads again only for another event", async () => {
  const value = client();
  const { observer } = watch(value);
  await vi.advanceTimersByTimeAsync(160);
  fail = true;
  const failure = expect(invalidateForServerEvent(value, [["tasks"]])).rejects.toMatchObject({
    status: 500,
  });
  await vi.advanceTimersByTimeAsync(1000);
  await failure;
  expect(starts).toHaveLength(2);
  fail = false;
  version = 2;
  const changed = invalidateForServerEvent(value, [["tasks"]]);
  await vi.advanceTimersByTimeAsync(1000);
  await changed;
  expect(observer.getCurrentResult().data).toBe(2);
});

it("keeps a newer event after an older refresh fails", async () => {
  const value = client();
  const { observer } = watch(value);
  await vi.advanceTimersByTimeAsync(160);
  fail = true;
  const first = invalidateForServerEvent(value, [["tasks"]]);
  await vi.advanceTimersByTimeAsync(10);
  fail = false;
  version = 2;
  const second = invalidateForServerEvent(value, [["tasks"]]);
  await vi.advanceTimersByTimeAsync(1000);
  await Promise.all([first, second]);
  expect(observer.getCurrentResult().data).toBe(2);
  expect(starts).toHaveLength(3);
});

it("bounds aggregate fast event reads across keys", async () => {
  latency = 0;
  const value = client();
  const keys = Array.from({ length: 8 }, (_, index) => `document-${index}`);
  const screens = keys.map((key) => watch(value, key));
  await vi.advanceTimersByTimeAsync(10);
  starts.length = 0;
  version = 1;
  const changed = invalidateForServerEvent(
    value,
    keys.map((key) => [key]),
  );
  await vi.advanceTimersByTimeAsync(2100);
  await changed;
  expect(starts).toHaveLength(8);
  for (let index = 2; index < starts.length; index += 1) {
    expect((starts[index] as number) - (starts[index - 2] as number)).toBeGreaterThanOrEqual(500);
  }
  expect(screens.every(({ observer }) => observer.getCurrentResult().data === 1)).toBe(true);
});

it("stops pending reads when the query client is cleared", async () => {
  const value = client();
  watch(value);
  await vi.advanceTimersByTimeAsync(160);
  const changed = invalidateForServerEvent(value, [["tasks"]]);
  value.clear();
  await vi.advanceTimersByTimeAsync(1000);
  await changed;
  expect(starts).toHaveLength(1);
});

it("keeps an event stale after unmount cancels its read", async () => {
  const value = client();
  const first = watch(value);
  await vi.advanceTimersByTimeAsync(160);
  version = 1;
  const changed = expect(invalidateForServerEvent(value, [["tasks"]])).rejects.toMatchObject({
    name: "EventReadCanceled",
  });
  await vi.advanceTimersByTimeAsync(10);
  first.stop();
  await vi.advanceTimersByTimeAsync(10);
  await changed;
  expect(active).toBe(0);
  const next = watch(value);
  await vi.advanceTimersByTimeAsync(660);
  expect(next.observer.getCurrentResult().data).toBe(1);
  expect(next.observer.getCurrentResult().error).toBeNull();
});

it("aborts scoped transport and leaves a new client independent when a session ends", async () => {
  const record = vi.fn();
  const value = client(record);
  watch(value);
  await vi.advanceTimersByTimeAsync(160);
  const changed = expect(invalidateForServerEvent(value, [["tasks"]])).rejects.toMatchObject({
    name: "EventRefreshStopped",
  });
  await vi.advanceTimersByTimeAsync(10);
  const stopFollowing = stops[0];
  if (stopFollowing === undefined) throw new Error("No router subscription");
  stopFollowing();
  await vi.advanceTimersByTimeAsync(10);
  await changed;
  expect(active).toBe(0);
  expect(record).not.toHaveBeenCalled();
  expect(value.getQueryState(["tasks"])?.fetchStatus).toBe("idle");
  version = 2;
  const next = watch(client());
  await vi.advanceTimersByTimeAsync(160);
  expect(next.observer.getCurrentResult().data).toBe(2);
});

it("charges each network retry to the event transport budget", async () => {
  latency = 0;
  const value = client();
  const observer = new QueryObserver(value, {
    queryKey: ["tasks"],
    queryFn: ({ signal }) => request<number>("/tasks", { signal }),
    retryDelay: 0,
  });
  stops.push(observer.subscribe(() => undefined));
  await vi.advanceTimersByTimeAsync(10);
  networkFailures = 3;
  version = 1;
  const changed = invalidateForServerEvent(value, [["tasks"]]);
  await vi.advanceTimersByTimeAsync(2100);
  await changed;
  expect(starts).toHaveLength(5);
  expect(observer.getCurrentResult().data).toBe(1);
  expect((starts[3] as number) - (starts[1] as number)).toBeGreaterThanOrEqual(500);
});

it("preserves an event permit when a person reads during its scheduled wait", async () => {
  const value = client();
  const { observer } = watch(value);
  await vi.advanceTimersByTimeAsync(160);
  version = 1;
  const first = invalidateForServerEvent(value, [["tasks"]]);
  await vi.advanceTimersByTimeAsync(160);
  await first;
  version = 2;
  const next = invalidateForServerEvent(value, [["tasks"]]).then(
    () => null,
    (error: unknown) => error,
  );
  await vi.advanceTimersByTimeAsync(10);
  window.dispatchEvent(new Event("click"));
  const person = observer.refetch();
  await vi.advanceTimersByTimeAsync(1000);
  await person;
  expect(await next).toBeNull();
  expect(observer.getCurrentResult().data).toBe(2);
  expect(observer.getCurrentResult().error).toBeNull();
});

it("leaves no spare event permit after a mutation replaces its active read", async () => {
  const value = client();
  const { observer } = watch(value);
  await vi.advanceTimersByTimeAsync(160);
  version = 1;
  const changed = invalidateForServerEvent(value, [["tasks"]]);
  await vi.advanceTimersByTimeAsync(10);
  window.dispatchEvent(new Event("click"));
  version = 2;
  const mutation = value
    .getMutationCache()
    .build(value, {
      mutationFn: async () => undefined,
      meta: { invalidates: [["tasks"]] },
    })
    .execute(undefined);
  await vi.advanceTimersByTimeAsync(1000);
  await Promise.all([changed, mutation]);
  expect(observer.getCurrentResult().data).toBe(2);
  const again = observer.refetch();
  await vi.advanceTimersByTimeAsync(1000);
  await again;
  expect(observer.getCurrentResult().error?.name).toBe("CycleStopped");
  expect(starts).toHaveLength(3);
});

it("lets a new observer consume a pending event during the spacing interval", async () => {
  const value = client();
  const first = watch(value);
  await vi.advanceTimersByTimeAsync(160);
  version = 1;
  const changed = invalidateForServerEvent(value, [["tasks"]]);
  await vi.advanceTimersByTimeAsync(160);
  await changed;
  version = 2;
  const next = invalidateForServerEvent(value, [["tasks"]]);
  await vi.advanceTimersByTimeAsync(10);
  const second = watch(value);
  await vi.advanceTimersByTimeAsync(10);
  expect(second.observer.getCurrentResult().error).toBeNull();
  await vi.advanceTimersByTimeAsync(1000);
  await next;
  expect(first.observer.getCurrentResult().data).toBe(2);
  expect(second.observer.getCurrentResult().data).toBe(2);
  expect(starts).toHaveLength(3);
});

it("resumes a canceled event only once without another cause", async () => {
  const value = client();
  const first = watch(value);
  await vi.advanceTimersByTimeAsync(160);
  version = 1;
  const changed = invalidateForServerEvent(value, [["tasks"]]).then(
    () => null,
    (failure: unknown) => failure,
  );
  await vi.advanceTimersByTimeAsync(10);
  first.stop();
  await vi.advanceTimersByTimeAsync(10);
  await changed;
  const second = watch(value);
  await vi.advanceTimersByTimeAsync(500);
  expect(active).toBe(1);
  second.stop();
  await vi.advanceTimersByTimeAsync(10);
  const third = watch(value);
  await vi.advanceTimersByTimeAsync(1000);
  expect(starts).toHaveLength(3);
  expect(third.observer.getCurrentResult().error?.name).toBe("CycleStopped");
});

it("rejects an active event refresh when cancellation reverts to old data", async () => {
  const value = client();
  const { observer } = watch(value);
  await vi.advanceTimersByTimeAsync(160);
  version = 1;
  const changed = expect(invalidateForServerEvent(value, [["tasks"]])).rejects.toMatchObject({
    name: "EventReadCanceled",
  });
  await vi.advanceTimersByTimeAsync(10);
  await value.cancelQueries({ queryKey: ["tasks"] });
  await vi.advanceTimersByTimeAsync(1000);
  await changed;
  expect(observer.getCurrentResult().data).toBe(0);
  expect(value.getQueryState(["tasks"])?.isInvalidated).toBe(true);
  expect(starts).toHaveLength(2);
});

it("discards an active event refresh when its query is removed", async () => {
  const value = client();
  watch(value);
  await vi.advanceTimersByTimeAsync(160);
  const changed = invalidateForServerEvent(value, [["tasks"]]);
  await vi.advanceTimersByTimeAsync(10);
  value.clear();
  await vi.advanceTimersByTimeAsync(1000);
  await changed;
  expect(active).toBe(0);
  expect(starts).toHaveLength(2);
});

it.each([false, true])(
  "preserves earlier page changes when pagination cancels a refresh: inactive=%s",
  async (inactive) => {
    const value = client();
    const observer = new InfiniteQueryObserver(value, {
      queryKey: ["pages"],
      queryFn: ({ signal, pageParam }) => request<number>(`/pages/${pageParam}`, { signal }),
      initialPageParam: 0,
      getNextPageParam: (_last, pages) => pages.length,
      retry: false,
    });
    let stop = observer.subscribe(() => undefined);
    stops.push(stop);
    await vi.advanceTimersByTimeAsync(160);
    if (inactive) stop();
    version = 1;
    const changed = invalidateForServerEvent(value, [["pages"]]).then(
      () => null,
      (failure: unknown) => failure,
    );
    if (inactive) {
      await changed;
      stop = observer.subscribe(() => undefined);
      stops.push(stop);
    }
    await vi.advanceTimersByTimeAsync(10);
    version = 2;
    const next = observer.fetchNextPage();
    await vi.advanceTimersByTimeAsync(2000);
    await next;
    expect(await changed).toBeNull();
    if (inactive) {
      expect(value.getQueryState(["pages"])?.isInvalidated).toBe(true);
      stop();
      stops.push(observer.subscribe(() => undefined));
      await vi.advanceTimersByTimeAsync(2000);
    }
    expect(observer.getCurrentResult().data?.pages).toEqual([2, 2]);
    expect(peak).toBe(1);
  },
);

it("follows a replacement of an event read started by a mount", async () => {
  const value = client();
  const first = watch(value);
  await vi.advanceTimersByTimeAsync(160);
  first.stop();
  version = 1;
  await invalidateForServerEvent(value, [["tasks"]]);
  const mounted = watch(value);
  const changed = invalidateForServerEvent(value, [["tasks"]]).then(
    () => null,
    (failure: unknown) => failure,
  );
  await vi.advanceTimersByTimeAsync(10);
  version = 2;
  window.dispatchEvent(new Event("click"));
  const replacement = mounted.observer.refetch();
  await vi.advanceTimersByTimeAsync(1000);
  await replacement;
  expect(await changed).toBeNull();
  expect(mounted.observer.getCurrentResult().data).toBe(2);
});

it("keeps an event cause when remounts cancel reads before budget admission", async () => {
  const value = client();
  let task = watch(value);
  watch(value, "first");
  watch(value, "second");
  await vi.advanceTimersByTimeAsync(160);
  version = 1;
  const occupying = invalidateForServerEvent(value, [["first"], ["second"]]);
  await vi.advanceTimersByTimeAsync(10);
  const changed = expect(invalidateForServerEvent(value, [["tasks"]])).rejects.toMatchObject({
    name: "EventReadCanceled",
  });
  await vi.advanceTimersByTimeAsync(10);
  task.stop();
  await vi.advanceTimersByTimeAsync(1);
  await changed;
  for (let mount = 0; mount < 3; mount += 1) {
    task = watch(value);
    await vi.advanceTimersByTimeAsync(10);
    task.stop();
    await vi.advanceTimersByTimeAsync(1);
  }
  const final = watch(value);
  await vi.advanceTimersByTimeAsync(1000);
  await occupying;
  expect(starts).toHaveLength(6);
  expect(final.observer.getCurrentResult().data).toBe(1);
  expect(final.observer.getCurrentResult().error).toBeNull();
  await final.observer.refetch();
  expect(final.observer.getCurrentResult().error?.name).toBe("CycleStopped");
});

it("spends the input cause on released event reads and grants no read from their answers", async () => {
  const value = client();
  const observed = [watch(value), watch(value, "first"), watch(value, "second")];
  await vi.advanceTimersByTimeAsync(160);
  version = 1;
  const occupying = invalidateForServerEvent(value, [["first"], ["second"]]);
  await vi.advanceTimersByTimeAsync(10);
  const queued = invalidateForServerEvent(value, [["tasks"]]);
  await vi.advanceTimersByTimeAsync(10);
  expect(starts).toHaveLength(5);
  window.dispatchEvent(new Event("click"));
  await vi.advanceTimersByTimeAsync(160);
  await Promise.all([occupying, queued]);
  expect(starts).toHaveLength(6);
  const task = observed[0];
  if (task === undefined) throw new Error("No task observer");
  expect(task.observer.getCurrentResult().data).toBe(1);
  await task.observer.refetch();
  expect(task.observer.getCurrentResult().error?.name).toBe("CycleStopped");
  expect(starts).toHaveLength(6);
});
