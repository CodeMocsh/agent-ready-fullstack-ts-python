import {
  focusManager,
  MutationCache,
  onlineManager,
  type Query,
  QueryCache,
  QueryClient,
} from "@tanstack/react-query";
import type { AnyRouter } from "@tanstack/react-router";
import type { RecordClientEvent } from "@/client-events";

const recordedTimes = new WeakMap<QueryClient, number>();

function mustReadAgainOnMount(queryClient: QueryClient, query: Query): boolean {
  const since = recordedTimes.get(queryClient);
  if (since === undefined) {
    throw new Error("this query client follows no router: call followNavigations(router, client)");
  }
  return query.state.isInvalidated || query.state.dataUpdatedAt < since;
}

export function createQueryClient(record: RecordClientEvent): QueryClient {
  const queryClient: QueryClient = new QueryClient({
    queryCache: new QueryCache({ onError: (error) => record("query", error) }),
    mutationCache: new MutationCache({ onError: (error) => record("mutation", error) }),
    defaultOptions: {
      queries: { refetchOnMount: (query) => mustReadAgainOnMount(queryClient, query) },
    },
  });
  return queryClient;
}

export function followNavigations(router: AnyRouter, queryClient: QueryClient): () => void {
  if (recordedTimes.has(queryClient)) {
    throw new Error("this query client already follows a router");
  }
  const recordTimeWhen = (happened: boolean) => {
    if (happened) {
      recordedTimes.set(queryClient, Date.now());
    }
  };
  recordTimeWhen(true);
  const stops = [
    router.subscribe("onBeforeNavigate", () => recordTimeWhen(router.state.status === "idle")),
    focusManager.subscribe(recordTimeWhen),
    onlineManager.subscribe(recordTimeWhen),
  ];
  return () => {
    for (const stop of stops) {
      stop();
    }
    recordedTimes.delete(queryClient);
  };
}
