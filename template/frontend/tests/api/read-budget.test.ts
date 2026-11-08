import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ReadBudget } from "@/api/read-budget";
import { createQueryClient } from "@/query-client";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

it("spaces a full interval of starts even when a read arrives just before its boundary", async () => {
  const budget = new ReadBudget({ intervalMs: 500, readsPerInterval: 2 });
  const signal = new AbortController().signal;
  const starts: number[] = [];
  const read = () =>
    budget.wait(signal).then(() => {
      starts.push(performance.now());
    });
  const first = read();
  await vi.advanceTimersByTimeAsync(1);
  await first;
  await vi.advanceTimersByTimeAsync(498);
  const second = read();
  await vi.advanceTimersByTimeAsync(1);
  await second;
  const last = Promise.all([read(), read()]);
  await vi.advanceTimersByTimeAsync(1);
  expect(starts).toEqual([0, 499, 500]);
  await vi.advanceTimersByTimeAsync(499);
  await last;
  expect(starts).toEqual([0, 499, 500, 999]);
});

it.each([
  { intervalMs: 0, readsPerInterval: 1 },
  { intervalMs: Number.NaN, readsPerInterval: 1 },
  { intervalMs: 500, readsPerInterval: 0 },
  { intervalMs: 500, readsPerInterval: 1.5 },
])("refuses an invalid event policy when creating the client: %j", (policy) => {
  expect(() => createQueryClient(vi.fn(), policy)).toThrow("Event reads need a positive interval");
});

it("releases queued reads for an outside cause without granting capacity to later events", async () => {
  const budget = new ReadBudget({ intervalMs: 500, readsPerInterval: 2 });
  const signal = new AbortController().signal;
  const first = Promise.all([budget.wait(signal), budget.wait(signal)]);
  await vi.advanceTimersByTimeAsync(1);
  await first;
  const waiting = Array.from({ length: 4 }, () => budget.wait(signal));
  budget.outsideCause();
  await Promise.all(waiting);
  let later = false;
  const next = budget.wait(signal).then(() => {
    later = true;
  });
  await vi.advanceTimersByTimeAsync(498);
  expect(later).toBe(false);
  await vi.advanceTimersByTimeAsync(1);
  await next;
  expect(later).toBe(true);
});
