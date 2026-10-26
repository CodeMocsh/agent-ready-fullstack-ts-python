import { focusManager, QueryObserver } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createQueryClient } from "@/query-client";

const TASKS = ["tasks"];
const THIRTY_SECONDS = 30_000;

function aTabShowingTasks() {
  const client = createQueryClient(vi.fn());
  const read = vi.fn(async () => "the tasks");
  client.mount();
  client.setQueryData(TASKS, "the tasks");
  const unsubscribe = new QueryObserver(client, { queryKey: TASKS, queryFn: read }).subscribe(
    () => undefined,
  );
  return {
    read,
    close: () => {
      unsubscribe();
      client.unmount();
    },
  };
}

async function refocus(): Promise<void> {
  focusManager.setFocused(false);
  focusManager.setFocused(true);
  await vi.advanceTimersByTimeAsync(0);
}

describe("the query client", () => {
  let tab: ReturnType<typeof aTabShowingTasks>;

  beforeEach(() => {
    vi.useFakeTimers();
    tab = aTabShowingTasks();
  });

  afterEach(() => {
    tab.close();
    focusManager.setFocused(undefined);
    vi.useRealTimers();
  });

  it("reads nothing again when a tab regains focus within 30 s", async () => {
    await vi.advanceTimersByTimeAsync(THIRTY_SECONDS - 1);
    await refocus();
    expect(tab.read).not.toHaveBeenCalled();
  });

  it("reads again what is older than 30 s when a tab regains focus", async () => {
    await vi.advanceTimersByTimeAsync(THIRTY_SECONDS + 1);
    await refocus();
    expect(tab.read).toHaveBeenCalledOnce();
  });
});
