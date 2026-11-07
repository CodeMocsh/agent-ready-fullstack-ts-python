import { focusManager, type QueryClient, QueryObserver } from "@tanstack/react-query";
import { createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { act } from "@testing-library/react";
import { afterEach, beforeEach, expect, it } from "vitest";
import { createQueryClient, followNavigations } from "@/query-client";

const BOARD = ["board"];
const SPACING_MS = 100;
const POLLS = 4;

let client: QueryClient;
let reads: number;
let failures: string[];
const unsubscribes: Array<() => void> = [];

function aBoardThatPolls(): void {
  const board = new QueryObserver(client, {
    queryKey: BOARD,
    queryFn: async () => {
      reads += 1;
      return reads;
    },
    refetchInterval: SPACING_MS,
  });
  unsubscribes.push(
    board.subscribe((result) => {
      if (result.error !== null) {
        failures.push(result.error.name);
      }
    }),
  );
}

function waitFor(milliseconds: number): Promise<void> {
  return act(() => new Promise<void>((resolve) => setTimeout(resolve, milliseconds)));
}

beforeEach(async () => {
  client = createQueryClient(() => undefined);
  client.mount();
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory() });
  unsubscribes.push(followNavigations(router, client));
  reads = 0;
  failures = [];
  await waitFor(10);
});

afterEach(() => {
  for (const unsubscribe of unsubscribes.splice(0)) unsubscribe();
  client.unmount();
  focusManager.setFocused(undefined);
});

it.each([
  [
    "a person's mutation of its key",
    () =>
      act(async () => {
        window.dispatchEvent(new Event("click"));
        await client
          .getMutationCache()
          .build(client, { mutationFn: async () => undefined, meta: { invalidates: [BOARD] } })
          .execute(undefined);
      }),
  ],
  [
    "a focus",
    () =>
      act(async () => {
        focusManager.setFocused(false);
        focusManager.setFocused(true);
      }),
  ],
])("keeps polling on TanStack's own timer after %s between two polls", async (_cause, cause) => {
  aBoardThatPolls();
  await waitFor(SPACING_MS * 1.5);

  await cause();
  await waitFor(SPACING_MS * POLLS);

  expect(failures).toEqual([]);
  expect(reads).toBeGreaterThanOrEqual(POLLS);
});
