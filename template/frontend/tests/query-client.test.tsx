import {
  focusManager,
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
import { createQueryClient, followNavigations } from "@/query-client";

const TASKS = ["tasks"];
const AS_READ = "the tasks as this tab read them";
const AS_CHANGED = "the tasks as another writer changed them";

let client: QueryClient;
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

function aQueryOfTasks() {
  const screen = new QueryObserver(client, tasksQuery());
  unsubscribes.push(screen.subscribe(() => undefined));
  return screen;
}

async function settle(): Promise<void> {
  vi.advanceTimersByTime(1);
  await act(() => new Promise((resolve) => setTimeout(resolve, 0)));
}

async function navigateTo(to: string): Promise<void> {
  await act(() => router.navigate({ to }));
  await settle();
}

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  client = createQueryClient(vi.fn());
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

it("reads again when a part mounts over a query this tab invalidated", async () => {
  aQueryOfTasks();
  await settle();
  held = AS_CHANGED;
  await client.invalidateQueries({ queryKey: TASKS, refetchType: "none" });
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
