import {
  focusManager,
  type Mutation,
  MutationCache,
  onlineManager,
  type Query,
  QueryCache,
  QueryClient,
  type QueryKey,
  type QueryPersister,
} from "@tanstack/react-query";
import type { AnyRouter } from "@tanstack/react-router";
import { Unreached } from "@/api/client";
import type { RecordClientEvent } from "@/client-events";

declare module "@tanstack/react-query" {
  interface Register {
    mutationMeta: { invalidates?: readonly QueryKey[] };
  }
}

type Read = { granted: boolean; at: number; failure: unknown; refused: boolean };

const NO_FAILURE = Symbol("no failure");

type Causes = {
  answeredSinceTheLast: boolean;
  reads: Map<string, Read>;
};

const UNFOLLOWED = "this query client follows no router: call followNavigations(router, client)";
const RETRIES_OF_AN_UNREACHED_READ = 3;
const CLOCK_RESOLUTION_MS = 1;
const PERSON_INPUTS = [
  "pointerdown",
  "pointerup",
  "keydown",
  "click",
  "submit",
  "change",
  "drop",
] as const;

const recordedTimes = new WeakMap<QueryClient, number>();
const followed = new WeakMap<QueryClient, Causes>();
const mutationsWithACause = new WeakSet<Mutation<unknown, unknown, unknown, unknown>>();

export class CycleStopped extends Error {
  override readonly name = "CycleStopped";

  constructor(queryKey: QueryKey) {
    super(
      `The read of ${JSON.stringify(queryKey)} stopped. It started again with no navigation, ` +
        "focus, reconnect, input, mutation of the key or server event about it since its last read.",
    );
  }
}

function causesOf(queryClient: QueryClient): Causes {
  const causes = followed.get(queryClient);
  if (causes === undefined) {
    throw new Error(UNFOLLOWED);
  }
  return causes;
}

function mustReadAgainOnMount(queryClient: QueryClient, query: Query): boolean {
  const since = recordedTimes.get(queryClient);
  if (since === undefined) {
    throw new Error(UNFOLLOWED);
  }
  return query.state.isInvalidated || query.state.dataUpdatedAt < since;
}

function causedBy(queryClient: QueryClient): void {
  const causes = causesOf(queryClient);
  causes.reads.clear();
  causes.answeredSinceTheLast = false;
}

function answered(queryClient: QueryClient): void {
  causesOf(queryClient).answeredSinceTheLast = true;
}

function grant(queryClient: QueryClient, queryKeys: readonly QueryKey[]): void {
  const { reads } = causesOf(queryClient);
  for (const queryKey of queryKeys) {
    for (const query of queryClient.getQueryCache().findAll({ queryKey })) {
      const last = reads.get(query.queryHash);
      if (last !== undefined) {
        last.granted = true;
      }
    }
  }
}

function shortestInterval(query: Query): number | undefined {
  const intervals = query.observers
    .map((observer) => {
      const interval = observer.options.refetchInterval;
      return typeof interval === "function" ? interval(query) : interval;
    })
    .filter((interval): interval is number => typeof interval === "number" && interval > 0);
  return intervals.length === 0 ? undefined : Math.min(...intervals);
}

function aRetryOfThisRead(query: Query, last: Read): boolean {
  if (query.state.fetchFailureCount === 0) {
    return false;
  }
  if (last.refused) {
    throw query.state.fetchFailureReason;
  }
  return true;
}

function aPageNotYetRead(query: Query): boolean {
  const meta = query.state.fetchMeta as { fetchMore?: unknown } | null;
  return meta?.fetchMore !== undefined;
}

function admit(queryClient: QueryClient, query: Query): Read {
  const { reads } = causesOf(queryClient);
  const now = Date.now();
  const last = reads.get(query.queryHash);
  if (last !== undefined && aRetryOfThisRead(query, last)) {
    return last;
  }
  if (last !== undefined && aPageNotYetRead(query)) {
    last.refused = false;
    return last;
  }
  if (last === undefined || last.granted) {
    const read = { granted: false, at: now, failure: NO_FAILURE, refused: false };
    reads.set(query.queryHash, read);
    return read;
  }
  const interval = shortestInterval(query);
  if (interval !== undefined && now - last.at >= interval - CLOCK_RESOLUTION_MS) {
    last.at = now;
    last.refused = false;
    return last;
  }
  last.refused = true;
  if (last.failure !== NO_FAILURE) {
    throw last.failure;
  }
  throw new CycleStopped(query.queryKey);
}

function everyListenerOfTheEventRan(): Promise<void> {
  return Promise.resolve();
}

const readsOnlyWithACause: QueryPersister<unknown, QueryKey> = async (queryFn, context, query) => {
  await everyListenerOfTheEventRan();
  const read = admit(context.client, query);
  try {
    const data = await queryFn(context);
    read.failure = NO_FAILURE;
    return data;
  } catch (failure: unknown) {
    read.failure = failure;
    throw failure;
  } finally {
    answered(context.client);
  }
};

function retriesOnlyAnUnreachedRead(failures: number, error: Error): boolean {
  return error instanceof Unreached && failures < RETRIES_OF_AN_UNREACHED_READ;
}

async function invalidate(queryClient: QueryClient, queryKeys: readonly QueryKey[]) {
  await Promise.all(queryKeys.map((queryKey) => queryClient.invalidateQueries({ queryKey })));
}

export async function invalidateForServerEvent(
  queryClient: QueryClient,
  queryKeys: readonly QueryKey[],
): Promise<void> {
  grant(queryClient, queryKeys);
  await invalidate(queryClient, queryKeys);
}

export function createQueryClient(record: RecordClientEvent): QueryClient {
  const queryClient: QueryClient = new QueryClient({
    queryCache: new QueryCache({ onError: (error) => record("query", error) }),
    mutationCache: new MutationCache({
      onMutate: (_variables, mutation) => {
        if (!causesOf(queryClient).answeredSinceTheLast) {
          mutationsWithACause.add(mutation);
        }
      },
      onError: (error) => record("mutation", error),
      onSettled: async (_data, _error, _variables, _context, mutation) => {
        const invalidates = mutation.meta?.invalidates;
        if (invalidates === undefined) {
          return;
        }
        if (mutationsWithACause.has(mutation)) {
          grant(queryClient, invalidates);
        }
        await invalidate(queryClient, invalidates);
      },
    }),
    defaultOptions: {
      queries: {
        refetchOnMount: (query) => mustReadAgainOnMount(queryClient, query),
        persister: readsOnlyWithACause,
        retry: retriesOnlyAnUnreachedRead,
      },
    },
  });
  return queryClient;
}

export function followNavigations(router: AnyRouter, queryClient: QueryClient): () => void {
  if (recordedTimes.has(queryClient)) {
    throw new Error("this query client already follows a router");
  }
  const recordTime = () => recordedTimes.set(queryClient, Date.now());
  const regained = (happened: boolean) => {
    if (happened) {
      recordTime();
      causedBy(queryClient);
    }
  };
  const personActed = () => causedBy(queryClient);
  let loading = false;
  followed.set(queryClient, { answeredSinceTheLast: false, reads: new Map() });
  recordTime();
  for (const input of PERSON_INPUTS) {
    window.addEventListener(input, personActed, { capture: true });
  }
  const stops = [
    router.subscribe("onBeforeLoad", () => {
      causedBy(queryClient);
      if (!loading) {
        recordTime();
      }
      loading = true;
    }),
    router.subscribe("onResolved", () => {
      loading = false;
    }),
    focusManager.subscribe(regained),
    onlineManager.subscribe(regained),
    () => {
      for (const input of PERSON_INPUTS) {
        window.removeEventListener(input, personActed, { capture: true });
      }
    },
  ];
  return () => {
    for (const stop of stops) {
      stop();
    }
    recordedTimes.delete(queryClient);
    followed.delete(queryClient);
  };
}
