import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";
import type { RecordClientEvent } from "@/client-events";

export function createQueryClient(record: RecordClientEvent): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache({ onError: (error) => record("query", error) }),
    mutationCache: new MutationCache({ onError: (error) => record("mutation", error) }),
  });
}
