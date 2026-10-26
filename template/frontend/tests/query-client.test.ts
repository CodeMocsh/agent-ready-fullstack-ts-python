import { focusManager, type QueryClient, QueryObserver } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createQueryClient } from "@/query-client";

const TASKS = ["tasks"];
const AS_READ = "the tasks as this tab read them";
const AS_CHANGED = "the tasks as another writer changed them";

let client: QueryClient;
let held: string;
const unsubscribes: Array<() => void> = [];

function aScreenShowingTasks() {
  const screen = new QueryObserver(client, { queryKey: TASKS, queryFn: async () => held });
  unsubscribes.push(screen.subscribe(() => undefined));
  return screen;
}

async function settle(): Promise<void> {
  await vi.advanceTimersByTimeAsync(0);
}

describe("the query client", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    client = createQueryClient(vi.fn());
    client.mount();
    held = AS_READ;
  });

  afterEach(() => {
    for (const unsubscribe of unsubscribes.splice(0)) unsubscribe();
    client.unmount();
    focusManager.setFocused(undefined);
    vi.useRealTimers();
  });

  it("shows another writer's change when a tab regains focus, however recent its read", async () => {
    const screen = aScreenShowingTasks();
    await settle();
    held = AS_CHANGED;
    focusManager.setFocused(false);
    focusManager.setFocused(true);
    await settle();
    expect(screen.getCurrentResult().data).toBe(AS_CHANGED);
  });

  it("shows another writer's change when a screen mounts over data it read just now", async () => {
    client.setQueryData(TASKS, AS_READ);
    held = AS_CHANGED;
    const screen = aScreenShowingTasks();
    await settle();
    expect(screen.getCurrentResult().data).toBe(AS_CHANGED);
  });
});
