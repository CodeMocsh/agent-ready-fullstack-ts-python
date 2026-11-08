import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { request, Unreached } from "@/api/client";
import { budgetEventRead, ReadBudget } from "@/api/read-budget";

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

function heldTransport() {
  let answer: (response: Response) => void = () => {
    throw new Error("No request started");
  };
  let signal: AbortSignal | null | undefined;
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementation((_url, init) => {
    signal = init?.signal;
    return new Promise((resolve, reject) => {
      answer = resolve;
      signal?.addEventListener("abort", () => reject(signal?.reason), { once: true });
    });
  });
  return {
    fetch,
    signal: () => signal,
    answer: (parts: unknown[]) => answer(new Response(JSON.stringify({ parts }))),
    body: (body: unknown) => answer(new Response(JSON.stringify(body))),
  };
}

const TASKS = { kind: "answered", status: 200, headers: {}, body: '{"tasks":[]}' };
const JOB = { kind: "answered", status: 200, headers: {}, body: '{"id":"1"}' };

it("keeps signaled reads in one group and cancels only one consumer", async () => {
  const transport = heldTransport();
  const canceled = new AbortController();
  const kept = new AbortController();
  const first = request("/tasks", { signal: canceled.signal });
  const second = request("/jobs/1", { signal: kept.signal });
  const refused = expect(first).rejects.toMatchObject({ name: "AbortError" });
  await vi.advanceTimersByTimeAsync(1);
  canceled.abort();
  await refused;
  expect(transport.signal()?.aborted).toBe(false);
  expect(transport.fetch).toHaveBeenCalledTimes(1);
  expect(String(transport.fetch.mock.calls[0]?.[0])).toContain("/reads?");
  transport.answer([TASKS, JOB]);
  await expect(second).resolves.toEqual({ id: "1" });
});

it("keeps another consumer of the same path after one cancels", async () => {
  const transport = heldTransport();
  const canceled = new AbortController();
  const first = request("/tasks", { signal: canceled.signal });
  const second = request("/tasks");
  const refused = expect(first).rejects.toMatchObject({ name: "AbortError" });
  canceled.abort();
  await refused;
  await vi.advanceTimersByTimeAsync(1);
  expect(transport.fetch).toHaveBeenCalledTimes(1);
  expect(String(transport.fetch.mock.calls[0]?.[0])).toMatch(/\/tasks$/);
  transport.body({ tasks: [{ id: "kept" }] });
  await expect(second).resolves.toEqual({ tasks: [{ id: "kept" }] });
});

it("aborts transport when every consumer cancels without reporting Unreached", async () => {
  const transport = heldTransport();
  const controller = new AbortController();
  const first = request("/tasks", { signal: controller.signal });
  const second = request("/jobs/1", { signal: controller.signal });
  const results = Promise.allSettled([first, second]);
  await vi.advanceTimersByTimeAsync(1);
  controller.abort();
  expect(transport.signal()?.aborted).toBe(true);
  for (const result of await results) {
    expect(result).toMatchObject({ status: "rejected", reason: { name: "AbortError" } });
    if (result.status === "rejected") expect(result.reason).not.toBeInstanceOf(Unreached);
  }
});

it("sends nothing for a group canceled before dispatch", async () => {
  const transport = heldTransport();
  const controller = new AbortController();
  const result = expect(request("/tasks", { signal: controller.signal })).rejects.toMatchObject({
    name: "AbortError",
  });
  controller.abort();
  await result;
  await vi.advanceTimersByTimeAsync(1);
  expect(transport.fetch).not.toHaveBeenCalled();
});

it("keeps cancellation connected until the response body finishes", async () => {
  let signal: AbortSignal | null | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (_url, init) => {
    signal = init?.signal;
    return new Response(
      new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode('{"tasks":'));
          signal?.addEventListener("abort", () => controller.error(signal?.reason), { once: true });
        },
      }),
    );
  });
  const controller = new AbortController();
  const result = expect(request("/tasks", { signal: controller.signal })).rejects.toMatchObject({
    name: "AbortError",
  });
  await vi.advanceTimersByTimeAsync(1);
  controller.abort();
  await result;
  expect(signal?.aborted).toBe(true);
});

it("preserves direct fetch cancellation", async () => {
  const transport = heldTransport();
  const controller = new AbortController();
  const result = expect(request("/direct", { signal: controller.signal })).rejects.toMatchObject({
    name: "AbortError",
  });
  controller.abort();
  await result;
  expect(transport.signal()?.aborted).toBe(true);
});

it("charges a grouped fallback to the same event read budget", async () => {
  const starts: number[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    starts.push(performance.now());
    return new Response(
      String(url).includes("/reads?")
        ? JSON.stringify({ parts: [TASKS, { kind: "left", reason: "timed out" }] })
        : '{"id":"1"}',
    );
  });
  const budget = new ReadBudget({ intervalMs: 500, readsPerInterval: 2 });
  const controller = new AbortController();
  budgetEventRead(controller.signal, budget);
  const answers = Promise.all([
    request("/tasks", { signal: controller.signal }),
    request("/jobs/1", { signal: controller.signal }),
  ]);
  await vi.advanceTimersByTimeAsync(10);
  expect(starts).toHaveLength(1);
  await vi.advanceTimersByTimeAsync(500);
  await expect(answers).resolves.toEqual([{ tasks: [] }, { id: "1" }]);
  expect(starts[1]).toBeGreaterThanOrEqual(500);
});

it("cancels a waiting fallback after another group consumer has its answer", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        parts: [TASKS, { kind: "left", reason: "timed out" }],
      }),
    ),
  );
  const budget = new ReadBudget({ intervalMs: 500, readsPerInterval: 2 });
  const controller = new AbortController();
  budgetEventRead(controller.signal, budget);
  const tasks = request("/tasks", { signal: controller.signal });
  const job = expect(request("/jobs/1", { signal: controller.signal })).rejects.toMatchObject({
    name: "AbortError",
  });
  await vi.advanceTimersByTimeAsync(10);
  await expect(tasks).resolves.toEqual({ tasks: [] });
  controller.abort();
  await job;
  await vi.advanceTimersByTimeAsync(1000);
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("keeps different clients in separate event transport groups", async () => {
  const requests: Array<{ signal: AbortSignal; answer: (value: Response) => void }> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((_url, init) => {
    const signal = init?.signal;
    if (signal === undefined || signal === null) throw new Error("No transport signal");
    return new Promise((resolve, reject) => {
      requests.push({ signal, answer: resolve });
      signal.addEventListener("abort", () => reject(signal.reason), { once: true });
    });
  });
  const oldBudget = new ReadBudget({ intervalMs: 500, readsPerInterval: 2 });
  const nextBudget = new ReadBudget({ intervalMs: 500, readsPerInterval: 2 });
  const oldSignal = new AbortController().signal;
  const nextSignal = new AbortController().signal;
  budgetEventRead(oldSignal, oldBudget);
  budgetEventRead(nextSignal, nextBudget);
  const old = expect(request("/tasks", { signal: oldSignal })).rejects.toMatchObject({
    name: "AbortError",
  });
  const next = request("/tasks", { signal: nextSignal });
  await vi.advanceTimersByTimeAsync(10);
  expect(requests).toHaveLength(2);
  oldBudget.lifetime.abort();
  await old;
  const second = requests[1];
  if (second === undefined) throw new Error("No second request");
  expect(second.signal.aborted).toBe(false);
  second.answer(new Response('{"tasks":[]}'));
  await expect(next).resolves.toEqual({ tasks: [] });
});

it("does not delay reads without an event cause when the event budget is full", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("1"));
  const budget = new ReadBudget({ intervalMs: 500, readsPerInterval: 1 });
  const signal = new AbortController().signal;
  budgetEventRead(signal, budget);
  const first = request("/event-first", { signal });
  const waiting = request("/event-next", { signal });
  await vi.advanceTimersByTimeAsync(10);
  await expect(first).resolves.toBe(1);
  await expect(request("/ordinary", { signal: new AbortController().signal })).resolves.toBe(1);
  expect(fetch).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(500);
  await expect(waiting).resolves.toBe(1);
  expect(fetch).toHaveBeenCalledTimes(3);
});
