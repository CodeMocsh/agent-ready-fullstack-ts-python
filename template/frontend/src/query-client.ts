import {
  focusManager,
  type Mutation,
  MutationCache,
  onlineManager,
  partialMatchKey,
  type Query,
  QueryCache,
  QueryClient,
  type QueryKey,
  type QueryPersister,
} from "@tanstack/react-query";
import type { AnyRouter } from "@tanstack/react-router";
import { Unreached } from "@/api/client";
import {
  budgetEventRead,
  checkedEventReadPolicy,
  EVENT_READ_POLICY,
  type EventReadPolicy,
} from "@/api/read-budget";
import type { RecordClientEvent } from "@/client-events";
import { EventRefresh } from "@/event-refresh";

declare module "@tanstack/react-query" {
  interface Register {
    mutationMeta: { invalidates?: readonly QueryKey[] };
  }
}

type Read = {
  granted: number;
  at: number;
  failure: unknown;
  refused: boolean;
  continuation: "none" | "available" | "used";
};

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

const eventPolicies = new WeakMap<QueryClient, EventReadPolicy>();
const eventRefreshes = new WeakMap<QueryClient, EventRefresh>();

const periods = new WeakMap<QueryClient, WeakSet<Query>>();
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

function answeredInThePeriod(queryClient: QueryClient): WeakSet<Query> {
  const answered = periods.get(queryClient);
  if (answered === undefined) {
    throw new Error(UNFOLLOWED);
  }
  return answered;
}

function startPeriod(queryClient: QueryClient): void {
  periods.set(queryClient, new WeakSet<Query>());
}

export function readIsCurrent(queryClient: QueryClient, query: Query): boolean {
  return answeredInThePeriod(queryClient).has(query) && !query.state.isInvalidated;
}

function staleUnlessCurrent(queryClient: QueryClient, query: Query): number {
  return readIsCurrent(queryClient, query) ? Number.POSITIVE_INFINITY : 0;
}

function causedBy(queryClient: QueryClient): void {
  const causes = causesOf(queryClient);
  causes.reads.clear();
  causes.answeredSinceTheLast = false;
  refreshesOf(queryClient).budget.outsideCause();
}

function answered(queryClient: QueryClient): void {
  causesOf(queryClient).answeredSinceTheLast = true;
}

function matching(queryKeys: readonly QueryKey[]): (query: Query) => boolean {
  return (query) => queryKeys.some((queryKey) => partialMatchKey(query.queryKey, queryKey));
}

function grant(queryClient: QueryClient, queryKeys: readonly QueryKey[]): void {
  const { reads } = causesOf(queryClient);
  const read = queryClient.getQueryCache().findAll({ predicate: matching(queryKeys) });
  for (const query of read) grantRead(reads, query.queryHash);
}

function grantRead(reads: Map<string, Read>, hash: string): void {
  const last = reads.get(hash);
  if (last === undefined) {
    reads.set(hash, { ...grantedRead(undefined, Date.now(), false), granted: 1 });
    return;
  }
  last.granted += 1;
  last.continuation = "none";
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

function admit(queryClient: QueryClient, query: Query, eventRead: boolean): Read {
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
  if (eventRead || last === undefined || last.granted > 0 || last.continuation === "available") {
    const read = grantedRead(last, now, eventRead);
    reads.set(query.queryHash, read);
    return read;
  }
  return admitBackstop(query, last, now);
}

function grantedRead(last: Read | undefined, now: number, eventRead: boolean): Read {
  const granted = last === undefined ? 0 : Math.max(0, last.granted - 1);
  const resumed = !eventRead && last?.granted === 0 && last.continuation === "available";
  return {
    granted,
    at: now,
    failure: NO_FAILURE,
    refused: false,
    continuation: resumed ? "used" : "none",
  };
}

function admitBackstop(query: Query, last: Read, now: number): Read {
  const interval = shortestInterval(query);
  if (interval !== undefined && now - last.at >= interval - CLOCK_RESOLUTION_MS) {
    last.at = now;
    last.refused = false;
    last.continuation = "none";
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

function refreshesOf(queryClient: QueryClient): EventRefresh {
  const refresh = eventRefreshes.get(queryClient);
  if (refresh === undefined) throw new Error(UNFOLLOWED);
  return refresh;
}

function permitFor(query: Query, refresh: EventRefresh) {
  if (aPageNotYetRead(query)) return undefined;
  const permit = refresh.take(query);
  if (permit !== undefined) return permit;
  return query.state.fetchFailureCount > 0 ? refresh.retry(query) : undefined;
}

function resumedForCause(client: QueryClient, query: Query, read: Read): void {
  read.granted = 0;
  read.at = Date.now();
  causesOf(client).reads.set(query.queryHash, read);
}

function watchUnanswered(
  client: QueryClient,
  query: Query,
  read: Read,
  signal: AbortSignal,
): () => void {
  const canceled = () => {
    if (
      followed.get(client)?.reads.get(query.queryHash) === read &&
      read.granted === 0 &&
      read.continuation === "none"
    ) {
      read.continuation = "available";
    }
  };
  signal.addEventListener("abort", canceled, { once: true });
  if (signal.aborted) canceled();
  return () => signal.removeEventListener("abort", canceled);
}

const readsOnlyWithACause: QueryPersister<unknown, QueryKey> = async (queryFn, context, query) => {
  await everyListenerOfTheEventRan();
  const refresh = refreshesOf(context.client);
  const permit = permitFor(query, refresh);
  const eventRead = permit !== undefined;
  const read = admit(context.client, query, eventRead);
  if (eventRead) {
    budgetEventRead(context.signal, refresh.budget, () =>
      resumedForCause(context.client, query, read),
    );
  }
  const attempt = refresh.begin(query, permit);
  const stop = eventRead
    ? refresh.watch(query, attempt, context.signal)
    : watchUnanswered(context.client, query, read, context.signal);
  try {
    if (eventRead) {
      await refresh.prepare(query, attempt, context.signal);
    }
    const data = await queryFn(context);
    read.failure = NO_FAILURE;
    return data;
  } catch (failure: unknown) {
    if (!context.signal.aborted) read.failure = failure;
    throw failure;
  } finally {
    stop();
    if (followed.has(context.client)) answered(context.client);
  }
};

function retriesOnlyAnUnreachedRead(failures: number, error: Error): boolean {
  return error instanceof Unreached && failures < RETRIES_OF_AN_UNREACHED_READ;
}

async function invalidate(queryClient: QueryClient, queryKeys: readonly QueryKey[]) {
  await queryClient.invalidateQueries({ predicate: matching(queryKeys) });
}

export async function invalidateForServerEvent(
  queryClient: QueryClient,
  queryKeys: readonly QueryKey[],
): Promise<void> {
  causesOf(queryClient);
  const refresh = refreshesOf(queryClient);
  await Promise.all(
    queryClient
      .getQueryCache()
      .findAll({ predicate: matching(queryKeys) })
      .map((query) => refresh.change(query)),
  );
}

async function invalidateAfterMutation(
  client: QueryClient,
  mutation: Mutation<unknown, unknown, unknown, unknown>,
): Promise<void> {
  const invalidates = mutation.meta?.invalidates;
  if (invalidates === undefined) return;
  if (mutationsWithACause.has(mutation)) grant(client, invalidates);
  await invalidate(client, invalidates);
}

export function createQueryClient(
  record: RecordClientEvent,
  eventReadPolicy: EventReadPolicy = EVENT_READ_POLICY,
): QueryClient {
  const queryClient: QueryClient = new QueryClient({
    queryCache: new QueryCache({ onError: (error) => record("query", error) }),
    mutationCache: new MutationCache({
      onMutate: (_variables, mutation) => {
        if (!causesOf(queryClient).answeredSinceTheLast) {
          mutationsWithACause.add(mutation);
        }
      },
      onError: (error) => record("mutation", error),
      onSettled: (_data, _error, _variables, _context, mutation) =>
        invalidateAfterMutation(queryClient, mutation),
    }),
    defaultOptions: {
      queries: {
        refetchOnMount: (query) => !readIsCurrent(queryClient, query),
        staleTime: (query) => staleUnlessCurrent(queryClient, query),
        persister: readsOnlyWithACause,
        retry: retriesOnlyAnUnreachedRead,
      },
    },
  });
  eventPolicies.set(queryClient, checkedEventReadPolicy(eventReadPolicy));
  return queryClient;
}

export function followNavigations(router: AnyRouter, queryClient: QueryClient): () => void {
  if (periods.has(queryClient)) {
    throw new Error("this query client already follows a router");
  }
  const policy = eventPolicies.get(queryClient);
  if (policy === undefined) throw new Error("Use createQueryClient before following a router");
  eventRefreshes.set(queryClient, new EventRefresh(queryClient, policy));
  const regained = (happened: boolean) => {
    if (happened) {
      startPeriod(queryClient);
      causedBy(queryClient);
    }
  };
  const personActed = () => causedBy(queryClient);
  let loading = false;
  followed.set(queryClient, { answeredSinceTheLast: false, reads: new Map() });
  startPeriod(queryClient);
  for (const input of PERSON_INPUTS) {
    window.addEventListener(input, personActed, { capture: true });
  }
  const stops = [
    router.subscribe("onBeforeLoad", () => {
      causedBy(queryClient);
      if (!loading) {
        startPeriod(queryClient);
      }
      loading = true;
    }),
    router.subscribe("onResolved", () => {
      loading = false;
    }),
    focusManager.subscribe(regained),
    onlineManager.subscribe(regained),
    queryClient.getQueryCache().subscribe((event) => {
      if (event.type === "updated" && event.action.type === "success") {
        answeredInThePeriod(queryClient).add(event.query);
      }
    }),
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
    eventRefreshes.get(queryClient)?.stop();
    eventRefreshes.delete(queryClient);
    periods.delete(queryClient);
    followed.delete(queryClient);
  };
}
