import {
  focusManager,
  onlineManager,
  type QueryClient,
  QueryObserver,
} from "@tanstack/react-query";
import { createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { act } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { RecordClientEvent } from "@/client-events";
import { createQueryClient, followNavigations, invalidateForServerEvent } from "@/query-client";

const TASKS = ["tasks"];
const AS_READ = "the tasks as this tab read them";
const AS_CHANGED = "the tasks as another writer changed them";

let client: QueryClient;
let record: RecordClientEvent;
let held: string;
let reads: number;
const unsubscribes: Array<() => void> = [];

function tasksQuery() {
  return {
    queryKey: TASKS,
    queryFn: async () => {
      reads += 1;
      return held;
    },
  };
}

function aQueryOfTasks() {
  const screen = new QueryObserver(client, tasksQuery());
  unsubscribes.push(screen.subscribe(() => undefined));
  return screen;
}

function aMutationOfTasks() {
  return client
    .getMutationCache()
    .build(client, { mutationFn: async () => undefined, meta: { invalidates: [TASKS] } })
    .execute(undefined);
}

async function settle(): Promise<void> {
  vi.advanceTimersByTime(1);
  await act(() => new Promise((resolve) => setTimeout(resolve, 0)));
}

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  record = vi.fn();
  client = createQueryClient(record);
  client.mount();
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory() });
  unsubscribes.push(followNavigations(router, client));
  await settle();
  held = AS_READ;
  reads = 0;
});

afterEach(() => {
  for (const unsubscribe of unsubscribes.splice(0)) unsubscribe();
  client.unmount();
  focusManager.setFocused(undefined);
  onlineManager.setOnline(true);
  vi.useRealTimers();
});

it("reads a query once when one mutation names two keys that both match it", async () => {
  aQueryOfTasks();
  await settle();
  window.dispatchEvent(new Event("click"));
  await act(() =>
    client
      .getMutationCache()
      .build(client, {
        mutationFn: async () => undefined,
        meta: { invalidates: [TASKS, [...TASKS]] },
      })
      .execute(undefined),
  );
  await settle();
  expect(reads).toBe(2);
  expect(record).not.toHaveBeenCalled();
});

it("leaves no read to spare when one mutation names two keys that both match a query", async () => {
  const screen = aQueryOfTasks();
  await settle();
  window.dispatchEvent(new Event("click"));
  await act(() =>
    client
      .getMutationCache()
      .build(client, {
        mutationFn: async () => undefined,
        meta: { invalidates: [TASKS, [...TASKS]] },
      })
      .execute(undefined),
  );
  await settle();
  await act(async () => {
    await screen.refetch();
  });
  await settle();
  expect(reads).toBe(2);
  expect(screen.getCurrentResult().error?.name).toBe("CycleStopped");
});

it("reads once for each mutation one click starts", async () => {
  aQueryOfTasks();
  await settle();
  window.dispatchEvent(new Event("click"));
  await act(() => Promise.all(Array.from({ length: 8 }, () => aMutationOfTasks())));
  await settle();
  expect(record).not.toHaveBeenCalled();
});

it("merges server events about a query before its read starts", async () => {
  aQueryOfTasks();
  await settle();
  await act(() =>
    Promise.all([
      invalidateForServerEvent(client, [TASKS]),
      invalidateForServerEvent(client, [TASKS]),
    ]),
  );
  await settle();
  expect(reads).toBe(2);
  expect(record).not.toHaveBeenCalled();
});

it("keeps a server event that arrives before a query is read again since the person acted", async () => {
  aQueryOfTasks();
  await settle();
  window.dispatchEvent(new Event("click"));
  await act(() =>
    Promise.all([
      invalidateForServerEvent(client, [TASKS]),
      invalidateForServerEvent(client, [TASKS]),
    ]),
  );
  await settle();
  expect(record).not.toHaveBeenCalled();
});

it("reads nothing when a part waiting on another read starts showing data read since the last cause", async () => {
  aQueryOfTasks();
  await settle();
  const waiting = new QueryObserver(client, { ...tasksQuery(), enabled: false });
  unsubscribes.push(waiting.subscribe(() => undefined));
  waiting.setOptions({ ...tasksQuery(), enabled: true });
  await settle();
  expect(reads).toBe(1);
  expect(record).not.toHaveBeenCalled();
});

it("reads again after a focus in the same millisecond as its read", async () => {
  const screen = aQueryOfTasks();
  await act(() => Promise.resolve());
  held = AS_CHANGED;
  focusManager.setFocused(false);
  focusManager.setFocused(true);
  await settle();
  expect(screen.getCurrentResult().data).toBe(AS_CHANGED);
});

it("takes a save started as a person leaves a field they changed as the person's mutation", async () => {
  aQueryOfTasks();
  await settle();
  window.dispatchEvent(new Event("change"));
  await act(() => aMutationOfTasks());
  await settle();
  expect(reads).toBe(2);
  expect(record).not.toHaveBeenCalled();
});

it("stops a cycle that moves the focus on each answer", async () => {
  const screen = aQueryOfTasks();
  unsubscribes.push(
    screen.subscribe((result) => {
      if (result.isSuccess && reads < 20) {
        window.dispatchEvent(new FocusEvent("focusout"));
        void aMutationOfTasks();
      }
    }),
  );
  await settle();
  await settle();
  await settle();
  expect(reads).toBeLessThan(20);
  expect(screen.getCurrentResult().error?.name).toBe("CycleStopped");
});

it("reads nothing for a part that mounts over data that a focus read answered in its own millisecond", async () => {
  aQueryOfTasks();
  await settle();
  focusManager.setFocused(false);
  focusManager.setFocused(true);
  await act(() => Promise.resolve());
  await act(() => Promise.resolve());
  const later = new QueryObserver(client, tasksQuery());
  unsubscribes.push(later.subscribe(() => undefined));
  await act(() => Promise.resolve());
  expect(later.getCurrentResult().error).toBeNull();
  expect(reads).toBe(2);
});

it("reads a list again when it mounts after a mutation that settled while it was gone", async () => {
  aQueryOfTasks();
  await settle();
  window.dispatchEvent(new Event("click"));
  let finish: (value: undefined) => void = () => undefined;
  const later = client
    .getMutationCache()
    .build(client, {
      mutationFn: () => new Promise<undefined>((resolve) => (finish = resolve)),
      meta: { invalidates: [TASKS] },
    })
    .execute(undefined);
  await act(() => aMutationOfTasks());
  await settle();
  for (const unsubscribe of unsubscribes.splice(1)) unsubscribe();
  await act(async () => {
    finish(undefined);
    await later;
  });
  const again = aQueryOfTasks();
  await settle();
  expect(again.getCurrentResult().error).toBeNull();
  expect(reads).toBe(3);
});

it("answers fetchQuery from the cache while its read is current", async () => {
  aQueryOfTasks();
  await settle();
  held = AS_CHANGED;
  const fetched = await act(() => client.fetchQuery(tasksQuery()));
  expect(fetched).toBe(AS_READ);
  expect(reads).toBe(1);
});
