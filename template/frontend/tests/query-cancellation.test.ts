import { QueryObserver } from "@tanstack/react-query";
import { createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { afterEach, expect, it, vi } from "vitest";
import { createQueryClient, followNavigations } from "@/query-client";

const cleanup: Array<() => void> = [];

afterEach(() => {
  for (const stop of cleanup.splice(0).reverse()) stop();
});

function pendingRead() {
  const client = createQueryClient(vi.fn());
  const stop = followNavigations(
    createRouter({ routeTree: createRootRoute(), history: createMemoryHistory() }),
    client,
  );
  cleanup.push(() => {
    stop();
    client.clear();
  });
  const answers: Array<(value: string) => void> = [];
  const queryKey = ["pending"];
  const queryFn = vi.fn(
    ({ signal }: { signal: AbortSignal }) =>
      new Promise<string>((resolve, reject) => {
        answers.push(resolve);
        signal.addEventListener("abort", () => reject(signal.reason), { once: true });
      }),
  );
  const mount = () => {
    const observer = new QueryObserver(client, { queryKey, queryFn, retry: false });
    const unmount = observer.subscribe(() => undefined);
    cleanup.push(unmount);
    return { observer, unmount };
  };
  return { client, queryKey, queryFn, answers, mount };
}

it("resumes an unanswered read after remount and grants nothing from its answer", async () => {
  const read = pendingRead();
  const first = read.mount();
  await vi.waitFor(() => expect(read.queryFn).toHaveBeenCalledTimes(1));
  first.unmount();
  const second = read.mount();
  await vi.waitFor(() => expect(read.queryFn).toHaveBeenCalledTimes(2));
  read.answers[1]?.("read");
  await vi.waitFor(() => expect(second.observer.getCurrentResult().data).toBe("read"));
  await second.observer.refetch();
  expect(second.observer.getCurrentResult().error?.name).toBe("CycleStopped");
  expect(read.queryFn).toHaveBeenCalledTimes(2);
});

it("refuses repeated canceled ordinary reads without a new cause", async () => {
  const read = pendingRead();
  const first = read.mount();
  await vi.waitFor(() => expect(read.queryFn).toHaveBeenCalledTimes(1));
  first.unmount();
  const second = read.mount();
  await vi.waitFor(() => expect(read.queryFn).toHaveBeenCalledTimes(2));
  second.unmount();
  const third = read.mount();
  await vi.waitFor(() =>
    expect(third.observer.getCurrentResult().error?.name).toBe("CycleStopped"),
  );
  expect(read.queryFn).toHaveBeenCalledTimes(2);
});

it("keeps a mutation cancellation from granting a spare read", async () => {
  const read = pendingRead();
  const mounted = read.mount();
  await vi.waitFor(() => expect(read.queryFn).toHaveBeenCalledTimes(1));
  read.answers[0]?.("first");
  await vi.waitFor(() => expect(mounted.observer.getCurrentResult().data).toBe("first"));
  window.dispatchEvent(new Event("click"));
  const active = mounted.observer.refetch();
  await vi.waitFor(() => expect(read.queryFn).toHaveBeenCalledTimes(2));
  const mutation = read.client
    .getMutationCache()
    .build(read.client, {
      mutationFn: async () => undefined,
      meta: { invalidates: [read.queryKey] },
    })
    .execute(undefined);
  await vi.waitFor(() => expect(read.queryFn).toHaveBeenCalledTimes(3));
  read.answers[2]?.("changed");
  await mutation;
  await active;
  await mounted.observer.refetch();
  expect(mounted.observer.getCurrentResult().error?.name).toBe("CycleStopped");
  expect(read.queryFn).toHaveBeenCalledTimes(3);
});
