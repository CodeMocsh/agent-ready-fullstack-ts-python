import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";
import type { RecordClientEvent } from "@/client-events";

const FRESH_FOR_MS = 30_000;

export const QUERY_DEFAULTS = { staleTime: FRESH_FOR_MS } as const;

export function createQueryClient(record: RecordClientEvent): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache({ onError: (error) => record("query", error) }),
    mutationCache: new MutationCache({ onError: (error) => record("mutation", error) }),
    defaultOptions: { queries: QUERY_DEFAULTS },
  });
}
