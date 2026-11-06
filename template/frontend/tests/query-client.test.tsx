import {
  focusManager,
  InfiniteQueryObserver,
  onlineManager,
  type QueryClient,
  QueryObserver,
} from "@tanstack/react-query";
import {
  type AnyRouter,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
  redirect,
} from "@tanstack/react-router";
import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiError, Unreached } from "@/api/client";
import type { RecordClientEvent } from "@/client-events";
import {
  CycleStopped,
  createQueryClient,
  followNavigations,
  invalidateForServerEvent,
} from "@/query-client";

const TASKS = ["tasks"];
const AS_READ = "the tasks as this tab read them";
const AS_CHANGED = "the tasks as another writer changed them";

let client: QueryClient;
let record: RecordClientEvent;
let router: AnyRouter;
let held: string;
let reads: number;
const unsubscribes: Array<() => void> = [];

function aRouter(): AnyRouter {
  const root = createRootRoute({ component: Outlet });
  const redirecting = createRoute({
    getParentRoute: () => root,
    path: "/",
    beforeLoad: async () => {
      await client.ensureQueryData(tasksQuery());
      vi.setSystemTime(Date.now() + 1);
      throw redirect({ href: "/tasks" });
    },
  });
  const tasks = createRoute({ getParentRoute: () => root, path: "/tasks" });
  const other = createRoute({ getParentRoute: () => root, path: "/other" });
  return createRouter({
    routeTree: root.addChildren([redirecting, tasks, other]),
    history: createMemoryHistory({ initialEntries: ["/tasks"] }),
  });
}

function tasksQuery() {
  return {
    queryKey: TASKS,
    queryFn: async () => {
      reads += 1;
      return held;
    },
  };
}

function aQueryOfTasks(options: { refetchInterval?: number } = {}) {
  const screen = new QueryObserver(client, { ...tasksQuery(), ...options });
  unsubscribes.push(screen.subscribe(() => undefined));
  return screen;
}

function aMutationOfTasks(mutationFn: () => Promise<undefined> = async () => undefined) {
  return client
    .getMutationCache()
    .build(client, { mutationFn, meta: { invalidates: [TASKS] } })
    .execute(undefined);
}

async function aPersonsMutationOfTasks(): Promise<void> {
  window.dispatchEvent(new Event("click"));
  await aMutationOfTasks();
}

async function readAgain(screen: { refetch: () => Promise<unknown> }): Promise<void> {
  await act(async () => {
    await screen.refetch();
  });
  await settle();
}

async function settle(): Promise<void> {
  vi.advanceTimersByTime(1);
  await act(() => new Promise((resolve) => setTimeout(resolve, 0)));
}

function aPerson(input: string): () => Promise<void> {
  return async () => void window.dispatchEvent(new Event(input));
}

async function navigateTo(to: string): Promise<void> {
  await act(() => router.navigate({ to }));
  await settle();
}

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  record = vi.fn();
  client = createQueryClient(record);
  client.mount();
  router = aRouter();
  unsubscribes.push(followNavigations(router, client));
  render(<RouterProvider router={router} />);
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

it("shows another writer's change when a tab regains focus, however recent its read", async () => {
  const screen = aQueryOfTasks();
  await settle();
  held = AS_CHANGED;
  focusManager.setFocused(false);
  focusManager.setFocused(true);
  await settle();
  expect(screen.getCurrentResult().data).toBe(AS_CHANGED);
});

it("shows another writer's change when a screen mounts after a navigation, however recent its read", async () => {
  client.setQueryData(TASKS, AS_READ);
  held = AS_CHANGED;
  await settle();
  await navigateTo("/other");
  const screen = aQueryOfTasks();
  await settle();
  expect(screen.getCurrentResult().data).toBe(AS_CHANGED);
});

it("shows another writer's change when a part mounts after a focus", async () => {
  client.setQueryData(TASKS, AS_READ);
  held = AS_CHANGED;
  await settle();
  focusManager.setFocused(false);
  focusManager.setFocused(true);
  await settle();
  const part = aQueryOfTasks();
  await settle();
  expect(part.getCurrentResult().data).toBe(AS_CHANGED);
});

it("reads once when a second part mounts after the first answer in the same navigation", async () => {
  aQueryOfTasks();
  await settle();
  aQueryOfTasks();
  await settle();
  expect(reads).toBe(1);
});

it("reads again when a part mounts over a query a mutation invalidated", async () => {
  aQueryOfTasks();
  await settle();
  for (const unsubscribe of unsubscribes.splice(1)) unsubscribe();
  held = AS_CHANGED;
  await act(aPersonsMutationOfTasks);
  const part = aQueryOfTasks();
  await settle();
  expect(part.getCurrentResult().data).toBe(AS_CHANGED);
});

it("keeps a read made before a redirect current for the screen it redirects to", async () => {
  await navigateTo("/");
  aQueryOfTasks();
  await settle();
  expect(router.state.location.pathname).toBe("/tasks");
  expect(reads).toBe(1);
});

it("refuses to mount a query over data on a client that follows no router", () => {
  const unfollowed = createQueryClient(vi.fn());
  unfollowed.setQueryData(TASKS, AS_READ);
  const screen = new QueryObserver(unfollowed, tasksQuery());
  expect(() => screen.subscribe(() => undefined)).toThrow(/follows no router/);
});

it("refuses to mount a query over data once the client stops following its router", () => {
  for (const unsubscribe of unsubscribes.splice(0)) unsubscribe();
  client.setQueryData(TASKS, AS_READ);
  expect(() => aQueryOfTasks()).toThrow(/follows no router/);
});

it("shows another writer's change when a part mounts after the tab comes back online", async () => {
  client.setQueryData(TASKS, AS_READ);
  held = AS_CHANGED;
  await settle();
  onlineManager.setOnline(false);
  onlineManager.setOnline(true);
  await settle();
  const part = aQueryOfTasks();
  await settle();
  expect(part.getCurrentResult().data).toBe(AS_CHANGED);
});

it("reads again when a query mounts over data older than the moment its client starts following", async () => {
  const local = createQueryClient(vi.fn());
  local.setQueryData(TASKS, AS_READ);
  await settle();
  unsubscribes.push(followNavigations(aRouter(), local));
  unsubscribes.push(new QueryObserver(local, tasksQuery()).subscribe(() => undefined));
  await settle();
  expect(reads).toBe(1);
});

it("refuses a second router for a client that already follows one", () => {
  expect(() => followNavigations(aRouter(), client)).toThrow(/already follows a router/);
});

it("shows another writer's change when a screen mounts after a navigation that follows a redirect", async () => {
  await navigateTo("/");
  client.setQueryData(TASKS, AS_READ);
  held = AS_CHANGED;
  await settle();
  await navigateTo("/other");
  const screen = aQueryOfTasks();
  await settle();
  expect(screen.getCurrentResult().data).toBe(AS_CHANGED);
});

it("shows another writer's change when a query mounts after the router reloads its routes", async () => {
  client.setQueryData(TASKS, AS_READ);
  held = AS_CHANGED;
  await settle();
  await act(() => router.invalidate());
  await settle();
  const screen = aQueryOfTasks();
  await settle();
  expect(screen.getCurrentResult().data).toBe(AS_CHANGED);
});

it("stops a read that starts again with no outside cause, draws it as an error, reports it and does not retry it", async () => {
  const screen = aQueryOfTasks();
  await settle();
  await readAgain(screen);
  const { error } = screen.getCurrentResult();
  expect(error).toBeInstanceOf(CycleStopped);
  expect(error?.message).toContain(JSON.stringify(TASKS));
  expect(record).toHaveBeenCalledWith("query", error);
  expect(reads).toBe(1);
});

it("stops a query that reads again each time its own answer arrives", async () => {
  const screen = aQueryOfTasks();
  unsubscribes.push(
    screen.subscribe((result) => {
      if (result.isSuccess && !result.isFetching) {
        void screen.refetch();
      }
    }),
  );
  await settle();
  await settle();
  expect(screen.getCurrentResult().error).toBeInstanceOf(CycleStopped);
  expect(reads).toBe(1);
});

it.each([
  ["a navigation", () => navigateTo("/other")],
  ["a person's pointer", aPerson("pointerdown")],
  ["a person's key", aPerson("keydown")],
  ["a person's click", aPerson("click")],
  ["a person's submit", aPerson("submit")],
  ["a person's release of a pointer", aPerson("pointerup")],
  ["a person's change of a field", aPerson("change")],
  ["a person's drop", aPerson("drop")],
])("reads again once after %s", async (_cause, cause) => {
  const screen = aQueryOfTasks();
  await settle();
  await cause();
  await readAgain(screen);
  expect(screen.getCurrentResult().error).toBeNull();
  expect(reads).toBe(2);
});

it.each([
  [
    "a focus",
    async () => {
      focusManager.setFocused(false);
      focusManager.setFocused(true);
    },
  ],
  [
    "a reconnect",
    async () => {
      onlineManager.setOnline(false);
      onlineManager.setOnline(true);
    },
  ],
  ["a person's mutation", () => act(aPersonsMutationOfTasks)],
  ["a server event", () => act(() => invalidateForServerEvent(client, [TASKS]))],
])("reads again after %s, and only once", async (_cause, cause) => {
  const screen = aQueryOfTasks();
  await settle();
  held = AS_CHANGED;
  await cause();
  await settle();
  expect(screen.getCurrentResult().data).toBe(AS_CHANGED);
  expect(reads).toBe(2);
  await readAgain(screen);
  expect(screen.getCurrentResult().error).toBeInstanceOf(CycleStopped);
});

it("reads a new key under the cause that read the last one", async () => {
  const first = new QueryObserver(client, { ...tasksQuery(), queryKey: ["tasks", 1] });
  unsubscribes.push(first.subscribe(() => undefined));
  await settle();
  first.setOptions({ ...tasksQuery(), queryKey: ["tasks", 2] });
  await settle();
  expect(first.getCurrentResult().error).toBeNull();
  expect(reads).toBe(2);
});

it("reads again with no outside cause once a refetch interval has passed, and not before", async () => {
  const backstop = 30_000;
  const screen = aQueryOfTasks({ refetchInterval: backstop });
  await settle();
  vi.setSystemTime(Date.now() + backstop);
  await readAgain(screen);
  expect(screen.getCurrentResult().error).toBeNull();
  await readAgain(screen);
  expect(screen.getCurrentResult().error).toBeInstanceOf(CycleStopped);
});

it("reads on its backstop no sooner than the interval after the read before it", async () => {
  const screen = aQueryOfTasks({ refetchInterval: 30_000 });
  await settle();
  await readAgain(screen);
  expect(screen.getCurrentResult().error).toBeInstanceOf(CycleStopped);
  expect(reads).toBe(1);
});

it("stops a mutation that starts when an answer arrives, at the read it causes", async () => {
  const screen = aQueryOfTasks();
  let mutated = false;
  unsubscribes.push(
    screen.subscribe((result) => {
      if (result.isSuccess && !mutated) {
        mutated = true;
        void aMutationOfTasks();
      }
    }),
  );
  await settle();
  await settle();
  expect(screen.getCurrentResult().error).toBeInstanceOf(CycleStopped);
  expect(reads).toBe(1);
});

it("lets a person's mutation read again only the keys it names", async () => {
  const other = new QueryObserver(client, { ...tasksQuery(), queryKey: ["other"] });
  unsubscribes.push(other.subscribe(() => undefined));
  await settle();
  let finish: (value: undefined) => void = () => undefined;
  window.dispatchEvent(new Event("click"));
  const mutation = aMutationOfTasks(() => new Promise((resolve) => (finish = resolve)));
  await readAgain(other);
  expect(other.getCurrentResult().error).toBeNull();
  await act(async () => {
    finish(undefined);
    await mutation;
  });
  await readAgain(other);
  expect(other.getCurrentResult().error).toBeInstanceOf(CycleStopped);
});

it("reads the next page of a list with no outside cause", async () => {
  const pages = new InfiniteQueryObserver(client, {
    queryKey: ["pages"],
    queryFn: async ({ pageParam }) => {
      reads += 1;
      return pageParam;
    },
    initialPageParam: 0,
    getNextPageParam: (last: number) => last + 1,
  });
  unsubscribes.push(pages.subscribe(() => undefined));
  await settle();
  await act(async () => {
    await pages.fetchNextPage();
  });
  await settle();
  expect(pages.getCurrentResult().error).toBeNull();
  expect(pages.getCurrentResult().data?.pages).toEqual([0, 1]);
});

it("retries a page that never reached the server after a stopped read of the same list", async () => {
  let unreachedOnce = true;
  const pages = new InfiniteQueryObserver(client, {
    queryKey: ["pages"],
    queryFn: async ({ pageParam }) => {
      reads += 1;
      if (pageParam === 1 && unreachedOnce) {
        unreachedOnce = false;
        throw new Unreached("GET /pages did not reach the server");
      }
      return pageParam;
    },
    initialPageParam: 0,
    getNextPageParam: (last: number) => last + 1,
    retryDelay: 0,
  });
  unsubscribes.push(pages.subscribe(() => undefined));
  await settle();
  await readAgain(pages);
  expect(pages.getCurrentResult().error).toBeInstanceOf(CycleStopped);
  await act(async () => {
    await pages.fetchNextPage();
  });
  await settle();
  expect(pages.getCurrentResult().error).toBeNull();
  expect(pages.getCurrentResult().data?.pages).toEqual([0, 1]);
});

it("stops a key that reads again with no outside cause after its query was removed", async () => {
  aQueryOfTasks();
  await settle();
  for (const unsubscribe of unsubscribes.splice(1)) unsubscribe();
  client.removeQueries({ queryKey: TASKS });
  const again = aQueryOfTasks();
  await settle();
  expect(again.getCurrentResult().error).toBeInstanceOf(CycleStopped);
  expect(reads).toBe(1);
});

it("keeps the failure of a read when a part mounts over it again under the same cause", async () => {
  const failing = () => ({
    queryKey: ["failing"],
    queryFn: async () => {
      reads += 1;
      throw new ApiError("Task not found", 500, null, null);
    },
  });
  unsubscribes.push(new QueryObserver(client, failing()).subscribe(() => undefined));
  await settle();
  for (const unsubscribe of unsubscribes.splice(1)) unsubscribe();
  const again = new QueryObserver(client, failing());
  unsubscribes.push(again.subscribe(() => undefined));
  await settle();
  expect(again.getCurrentResult().error).toBeInstanceOf(ApiError);
  expect(reads).toBe(1);
});

it("sends nothing when a part mounts again over a read that never reached the server", async () => {
  const unreached = () => ({
    queryKey: ["unreached"],
    queryFn: async () => {
      reads += 1;
      throw new Unreached("GET /tasks did not reach the server");
    },
    retryDelay: 0,
  });
  const first = new QueryObserver(client, unreached());
  unsubscribes.push(first.subscribe(() => undefined));
  await vi.waitFor(() => expect(first.getCurrentResult().isError).toBe(true));
  expect(reads).toBe(4);
  for (const unsubscribe of unsubscribes.splice(1)) unsubscribe();
  const again = new QueryObserver(client, unreached());
  unsubscribes.push(again.subscribe(() => undefined));
  await vi.waitFor(() => expect(again.getCurrentResult().isError).toBe(true));
  await settle();
  expect(again.getCurrentResult().error).toBeInstanceOf(Unreached);
  expect(reads).toBe(4);
});

it("does not retry a stopped read, whatever retry the query sets", async () => {
  const screen = new QueryObserver(client, { ...tasksQuery(), retry: 3, retryDelay: 0 });
  unsubscribes.push(screen.subscribe(() => undefined));
  await settle();
  await readAgain(screen);
  await settle();
  expect(screen.getCurrentResult().error).toBeInstanceOf(CycleStopped);
  expect(reads).toBe(1);
});

it.each([
  ["a refusal", () => new ApiError("Task not found", 404, null, null)],
  ["an answer it cannot read", () => new SyntaxError("Unexpected token < in JSON")],
])("retries a read that never reached the server, and not %s", async (_answer, answer) => {
  const unanswered = new QueryObserver(client, {
    queryKey: ["unanswered"],
    queryFn: async () => {
      reads += 1;
      if (reads === 1) {
        throw new Unreached("GET /tasks did not reach the server");
      }
      return held;
    },
    retryDelay: 0,
  });
  unsubscribes.push(unanswered.subscribe(() => undefined));
  await settle();
  await settle();
  expect(unanswered.getCurrentResult().data).toBe(AS_READ);
  const refused = new QueryObserver(client, {
    queryKey: ["refused"],
    queryFn: async () => {
      reads += 1;
      throw answer();
    },
    retryDelay: 0,
  });
  unsubscribes.push(refused.subscribe(() => undefined));
  await settle();
  await settle();
  expect(refused.getCurrentResult().error?.name).toBe(answer().name);
  expect(reads).toBe(3);
});

it("refuses to read on a client that follows no router", async () => {
  const unfollowed = createQueryClient(vi.fn());
  await expect(unfollowed.fetchQuery(tasksQuery())).rejects.toThrow(/follows no router/);
});
