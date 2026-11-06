import { focusManager } from "@tanstack/react-query";
import {
  type AnyRouter,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { act, render } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BuildNotLoaded, BuildUnknown, followTheDeployedBuild } from "@/build";
import type { Session } from "@/missing-chunk";
import { server } from "@/mocks/node";

const BUNDLE = "https://example.test/app/";
const FILE = new URL("build.json", BUNDLE);
const RUNNING = `${BUNDLE}assets/index-abc123.js`;
const THE_NEXT_ENTRY = "assets/index-def456.js";

let router: AnyRouter;
let stop: () => void;
let answered: number;

function aSession(): () => Session {
  const held = new Map<string, string>();
  const session: Session = {
    getItem: (key) => held.get(key) ?? null,
    setItem: (key, value) => {
      held.set(key, value);
    },
  };
  return () => session;
}

function deploying(answer: () => Response): void {
  server.use(
    http.get(FILE.href, () => {
      answered += 1;
      return answer();
    }),
  );
}

function aRouter(): AnyRouter {
  const root = createRootRoute({ component: Outlet });
  const one = createRoute({ getParentRoute: () => root, path: "/one" });
  const two = createRoute({ getParentRoute: () => root, path: "/two" });
  return createRouter({
    routeTree: root.addChildren([one, two]),
    history: createMemoryHistory({ initialEntries: ["/one"] }),
  });
}

function aTab(session: () => Session, running = RUNNING) {
  const reload = vi.fn();
  const record = vi.fn();
  stop = followTheDeployedBuild({
    router,
    tab: { location: { reload } },
    session,
    record,
    build: { running, file: FILE },
  });
  return { reload, record };
}

function focus(): void {
  focusManager.setFocused(false);
  focusManager.setFocused(true);
}

async function navigateTo(to: string): Promise<void> {
  await act(() => router.navigate({ to }));
}

async function checked(times: number): Promise<void> {
  await vi.waitFor(() => expect(answered).toBe(times));
  await act(() => new Promise((resolve) => setTimeout(resolve, 10)));
}

beforeEach(async () => {
  answered = 0;
  router = aRouter();
  render(<RouterProvider router={router} />);
  await act(() => router.load());
});

afterEach(() => {
  stop();
  focusManager.setFocused(undefined);
});

it("a tab does not reload while it runs the deployed build", async () => {
  deploying(() => HttpResponse.json({ entry: "assets/index-abc123.js" }));
  const tab = aTab(aSession());

  focus();
  await checked(1);
  await navigateTo("/two");
  await checked(2);
  await navigateTo("/one");

  expect(tab.reload).not.toHaveBeenCalled();
  expect(tab.record).not.toHaveBeenCalled();
});

it("a tab reloads at the next navigation after it learns of a new build, and not before", async () => {
  deploying(() => HttpResponse.json({ entry: THE_NEXT_ENTRY }));
  const tab = aTab(aSession());

  focus();
  await checked(1);

  expect(tab.reload).not.toHaveBeenCalled();

  await navigateTo("/two");

  expect(tab.reload).toHaveBeenCalledOnce();
});

it("a tab learns of a new build when a navigation starts", async () => {
  deploying(() => HttpResponse.json({ entry: THE_NEXT_ENTRY }));
  const tab = aTab(aSession());

  await navigateTo("/two");
  await checked(1);

  expect(tab.reload).not.toHaveBeenCalled();

  await navigateTo("/one");

  expect(tab.reload).toHaveBeenCalledOnce();
});

it("a tab reloads into one build once, and reports a host that still serves the old build", async () => {
  deploying(() => HttpResponse.json({ entry: THE_NEXT_ENTRY }));
  const session = aSession();
  const first = aTab(session);
  focus();
  await checked(1);
  await navigateTo("/two");
  expect(first.reload).toHaveBeenCalledOnce();
  stop();
  const reloaded = aTab(session);

  focus();
  await checked(2);
  focus();
  await checked(3);
  await navigateTo("/one");

  expect(reloaded.reload).not.toHaveBeenCalled();
  expect(reloaded.record).toHaveBeenCalledOnce();
  expect(reloaded.record).toHaveBeenCalledWith("build", expect.any(BuildNotLoaded));
});

it("a tab that moved on from a build reloads into it again when a deploy goes back to it", async () => {
  let entry = THE_NEXT_ENTRY;
  deploying(() => HttpResponse.json({ entry }));
  const session = aSession();
  const first = aTab(session);
  focus();
  await checked(1);
  await navigateTo("/two");
  stop();
  entry = "assets/index-ghi789.js";
  const second = aTab(session, `${BUNDLE}${THE_NEXT_ENTRY}`);
  focus();
  await checked(2);
  await navigateTo("/one");
  stop();
  entry = THE_NEXT_ENTRY;
  const third = aTab(session, `${BUNDLE}assets/index-ghi789.js`);

  focus();
  await checked(3);
  await navigateTo("/two");

  expect([first, second, third].map((tab) => tab.reload.mock.calls.length)).toEqual([1, 1, 1]);
  expect(third.record).not.toHaveBeenCalled();
});

it("a tab follows a deploy that goes forward, back, and forward again", async () => {
  let entry = THE_NEXT_ENTRY;
  deploying(() => HttpResponse.json({ entry }));
  const session = aSession();
  const first = aTab(session);
  focus();
  await checked(1);
  await navigateTo("/two");
  stop();
  entry = "assets/index-abc123.js";
  const second = aTab(session, `${BUNDLE}${THE_NEXT_ENTRY}`);
  focus();
  await checked(2);
  await navigateTo("/one");
  stop();
  entry = THE_NEXT_ENTRY;
  const third = aTab(session);

  focus();
  await checked(3);
  await navigateTo("/two");

  expect([first, second, third].map((tab) => tab.reload.mock.calls.length)).toEqual([1, 1, 1]);
  expect(third.record).not.toHaveBeenCalled();
});

it("a tab that learned of a new build stays when the deploy goes back to its own", async () => {
  let entry = THE_NEXT_ENTRY;
  deploying(() => HttpResponse.json({ entry }));
  const tab = aTab(aSession());
  focus();
  await checked(1);

  entry = "assets/index-abc123.js";
  focus();
  await checked(2);
  await navigateTo("/two");

  expect(tab.reload).not.toHaveBeenCalled();
});

it.each([
  ["a reload of the routes", () => act(() => router.invalidate())],
  ["a change of search parameters", () => act(() => router.history.push("/one?q=a"))],
])("a tab that learned of a new build does not reload at %s", async (_navigation, navigate) => {
  deploying(() => HttpResponse.json({ entry: THE_NEXT_ENTRY }));
  const tab = aTab(aSession());
  focus();
  await checked(1);

  await navigate();

  expect(tab.reload).not.toHaveBeenCalled();
});

it("a tab reports a build file that is not JSON, and does not reload", async () => {
  deploying(
    () => new HttpResponse("<!doctype html>", { headers: { "content-type": "text/html" } }),
  );
  const tab = aTab(aSession());

  focus();
  await vi.waitFor(() =>
    expect(tab.record).toHaveBeenCalledWith("build", expect.any(BuildUnknown)),
  );
  await navigateTo("/two");

  expect(tab.reload).not.toHaveBeenCalled();
});

it("a tab reports a build file that does not answer, and does not reload", async () => {
  deploying(() => new HttpResponse(null, { status: 503 }));
  const tab = aTab(aSession());

  focus();
  await vi.waitFor(() =>
    expect(tab.record).toHaveBeenCalledWith("build", expect.any(BuildUnknown)),
  );
  await navigateTo("/two");

  expect(tab.reload).not.toHaveBeenCalled();
});

it("a tab reports a build file that names no entry, and does not reload", async () => {
  deploying(() => HttpResponse.json({ build: THE_NEXT_ENTRY }));
  const tab = aTab(aSession());

  focus();
  await vi.waitFor(() =>
    expect(tab.record).toHaveBeenCalledWith("build", expect.any(BuildUnknown)),
  );
  await navigateTo("/two");

  expect(tab.reload).not.toHaveBeenCalled();
});
