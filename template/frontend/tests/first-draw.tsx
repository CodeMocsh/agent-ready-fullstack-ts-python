import { focusManager, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRouter,
  RouterProvider,
  rootRouteId,
} from "@tanstack/react-router";
import { render, waitFor } from "@testing-library/react";
import { API_BASE_URL } from "@/api/base";
import { AppProviders } from "@/app-providers";
import { server } from "@/mocks/node";
import { createQueryClient } from "@/query-client";
import { router } from "@/router";

export type ReadBudget = { visit: string; reads: number; lands?: string };

export type Reads = { landed: string; firstDraw: string[]; afterFocus: string[] };

type AppRouter = typeof router;

type RequestEvent = { request: Request; requestId: string };

type ResponseEvent = RequestEvent & { response: Response };

const QUIET_MS = 150;

export function screens(): string[] {
  return Object.values(router.routesById)
    .filter((route) => route.children === undefined)
    .map((route) => route.fullPath);
}

function contextFor(queryClient: QueryClient): AppRouter["options"]["context"] {
  return Object.fromEntries(
    Object.entries(router.options.context ?? {}).map(([name, value]) => [
      name,
      value instanceof QueryClient ? queryClient : value,
    ]),
  ) as AppRouter["options"]["context"];
}

function failureOf(visited: AppRouter, visit: string, failures: unknown[]): unknown {
  const [first] = failures;
  if (first !== undefined) {
    return first;
  }
  const { matches } = visited.state;
  const failed = matches.find((match) => match.status === "error");
  if (failed !== undefined) {
    return failed.error;
  }
  if (matches.some((match) => match.status === "notFound")) {
    return new Error(`${visit} draws the not-found screen`);
  }
  return undefined;
}

async function settled(
  visited: AppRouter,
  visit: string,
  queryClient: QueryClient,
  open: Map<string, string>,
  failures: unknown[],
): Promise<void> {
  let quietSince: number | undefined;
  await waitFor(
    () => {
      if (failureOf(visited, visit, failures) !== undefined) {
        return;
      }
      const quiet =
        visited.state.status === "idle" &&
        !visited.state.isLoading &&
        queryClient.isFetching() === 0 &&
        open.size === 0;
      if (!quiet) {
        quietSince = undefined;
        throw new Error(
          `the draw did not settle: router ${visited.state.status}, ` +
            `${queryClient.isFetching()} queries fetching, open: ${[...open.values()].join(", ")}`,
        );
      }
      quietSince ??= performance.now();
      if (performance.now() - quietSince < QUIET_MS) {
        throw new Error(`the draw has been quiet for less than ${QUIET_MS} ms`);
      }
    },
    { interval: 20 },
  );
  const failure = failureOf(visited, visit, failures);
  if (failure !== undefined) {
    throw failure;
  }
}

type Recorder = {
  open: Map<string, string>;
  failures: unknown[];
  takeReads: () => string[];
  stop: () => void;
};

function recordRequests(streams: readonly RegExp[]): Recorder {
  const api = new URL(API_BASE_URL, window.location.href).href;
  const open = new Map<string, string>();
  const failures: unknown[] = [];
  const reads: string[] = [];
  const pathOf = (request: Request) => `${request.method} ${new URL(request.url).pathname}`;

  const started = ({ request, requestId }: RequestEvent) => {
    if (streams.some((stream) => stream.test(new URL(request.url).pathname))) {
      return;
    }
    open.set(requestId, pathOf(request));
    if (request.method === "GET" && request.url.startsWith(api)) {
      reads.push(pathOf(request));
    }
  };
  const ended = ({ requestId }: { requestId: string }) => {
    open.delete(requestId);
  };
  const unanswered = ({ request }: RequestEvent) => {
    failures.push(new Error(`no handler in src/mocks answers ${pathOf(request)}`));
  };
  const refused = ({ request, response }: ResponseEvent) => {
    if (response.status >= 400) {
      failures.push(new Error(`${pathOf(request)} answered ${response.status}`));
    }
  };
  const ending = ["request:end", "request:unhandled", "unhandledException"] as const;

  server.events.on("request:start", started);
  server.events.on("request:unhandled", unanswered);
  server.events.on("response:mocked", refused);
  for (const event of ending) {
    server.events.on(event, ended);
  }
  return {
    open,
    failures,
    takeReads: () => reads.splice(0),
    stop: () => {
      server.events.removeListener("request:start", started);
      server.events.removeListener("request:unhandled", unanswered);
      server.events.removeListener("response:mocked", refused);
      for (const event of ending) {
        server.events.removeListener(event, ended);
      }
    },
  };
}

export async function readsOf(visit: string, streams: readonly RegExp[]): Promise<Reads> {
  const recorder = recordRequests(streams);
  const { open, failures } = recorder;
  try {
    const queryClient = createQueryClient((_kind, error) => failures.push(error));
    const defaults = queryClient.getDefaultOptions();
    queryClient.setDefaultOptions({ ...defaults, queries: { ...defaults.queries, retry: false } });
    const visited = createRouter({
      ...router.options,
      history: createMemoryHistory({ initialEntries: [visit] }),
      context: contextFor(queryClient),
    });
    const drawn = render(
      <QueryClientProvider client={queryClient}>
        <AppProviders>
          <RouterProvider router={visited} />
        </AppProviders>
      </QueryClientProvider>,
      { onCaughtError: (error) => failures.push(error) },
    );
    try {
      await settled(visited, visit, queryClient, open, failures);
      const firstDraw = recorder.takeReads();
      focusManager.setFocused(false);
      focusManager.setFocused(true);
      await settled(visited, visit, queryClient, open, failures);
      const landed = visited.state.matches.at(-1);
      if (landed === undefined || landed.routeId === rootRouteId) {
        throw new Error(`${visit} matches no screen`);
      }
      return { landed: landed.fullPath, firstDraw, afterFocus: recorder.takeReads() };
    } finally {
      drawn.unmount();
    }
  } finally {
    recorder.stop();
    focusManager.setFocused(undefined);
  }
}
