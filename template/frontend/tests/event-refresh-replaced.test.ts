import { expect, it, vi } from "vitest";
import { invalidateForServerEvent } from "@/query-client";
import { client, mutateTasks, serveEventReads, server, watch } from "./event-reads.ts";

serveEventReads();

it("preserves an event permit when a person reads during its scheduled wait", async () => {
  const value = client();
  const { observer } = watch(value);
  await vi.advanceTimersByTimeAsync(160);
  server.version = 1;
  const first = invalidateForServerEvent(value, [["tasks"]]);
  await vi.advanceTimersByTimeAsync(160);
  await first;
  server.version = 2;
  const next = invalidateForServerEvent(value, [["tasks"]]).then(
    () => null,
    (error: unknown) => error,
  );
  await vi.advanceTimersByTimeAsync(10);
  window.dispatchEvent(new Event("click"));
  const person = observer.refetch();
  await vi.advanceTimersByTimeAsync(1000);
  await person;
  expect(await next).toBeNull();
  expect(observer.getCurrentResult().data).toBe(2);
  expect(observer.getCurrentResult().error).toBeNull();
});

it("leaves no spare event permit after a mutation replaces its active read", async () => {
  const value = client();
  const { observer } = watch(value);
  await vi.advanceTimersByTimeAsync(160);
  server.version = 1;
  const changed = invalidateForServerEvent(value, [["tasks"]]);
  await vi.advanceTimersByTimeAsync(10);
  server.version = 2;
  const mutation = mutateTasks(value);
  await vi.advanceTimersByTimeAsync(1000);
  await Promise.all([changed, mutation]);
  expect(observer.getCurrentResult().data).toBe(2);
  const again = observer.refetch();
  await vi.advanceTimersByTimeAsync(1000);
  await again;
  expect(observer.getCurrentResult().error?.name).toBe("CycleStopped");
  expect(server.starts).toHaveLength(3);
});

it("ends an event refresh when a mutation's read replaces its resumed read", async () => {
  const value = client();
  const { observer } = watch(value);
  await vi.advanceTimersByTimeAsync(160);
  server.version = 1;
  const changed = invalidateForServerEvent(value, [["tasks"]]);
  await vi.advanceTimersByTimeAsync(10);
  const first = mutateTasks(value);
  await vi.advanceTimersByTimeAsync(500);
  server.version = 2;
  const second = mutateTasks(value);
  await vi.advanceTimersByTimeAsync(1000);
  await Promise.all([changed, first, second]);
  expect(observer.getCurrentResult().data).toBe(2);
  expect(server.starts).toHaveLength(4);
  await observer.refetch();
  expect(observer.getCurrentResult().error?.name).toBe("CycleStopped");
});

it("reads a change again when a mutation's read that replaced its event read is canceled", async () => {
  const value = client();
  const { observer } = watch(value);
  await vi.advanceTimersByTimeAsync(160);
  server.version = 1;
  const changed = invalidateForServerEvent(value, [["tasks"]]);
  await vi.advanceTimersByTimeAsync(10);
  const first = mutateTasks(value);
  await vi.advanceTimersByTimeAsync(500);
  const second = mutateTasks(value);
  await vi.advanceTimersByTimeAsync(10);
  server.version = 2;
  await value.cancelQueries({ queryKey: ["tasks"] });
  await vi.advanceTimersByTimeAsync(1000);
  await Promise.all([changed, first, second]);
  expect(observer.getCurrentResult().data).toBe(2);
});

it("fails an event refresh with its refusal when two cancellations leave it no cause to read", async () => {
  const value = client();
  watch(value);
  await vi.advanceTimersByTimeAsync(160);
  server.version = 1;
  const changed = invalidateForServerEvent(value, [["tasks"]]).then(
    () => null,
    (failure: unknown) => failure,
  );
  await vi.advanceTimersByTimeAsync(10);
  const first = mutateTasks(value);
  await vi.advanceTimersByTimeAsync(500);
  const second = mutateTasks(value);
  await vi.advanceTimersByTimeAsync(10);
  await value.cancelQueries({ queryKey: ["tasks"] });
  await vi.advanceTimersByTimeAsync(10);
  await value.cancelQueries({ queryKey: ["tasks"] });
  await vi.advanceTimersByTimeAsync(1000);
  await Promise.all([first, second]);
  expect(await changed).toMatchObject({ name: "CycleStopped" });
});

it("lets a new observer consume a pending event during the spacing interval", async () => {
  const value = client();
  const first = watch(value);
  await vi.advanceTimersByTimeAsync(160);
  server.version = 1;
  const changed = invalidateForServerEvent(value, [["tasks"]]);
  await vi.advanceTimersByTimeAsync(160);
  await changed;
  server.version = 2;
  const next = invalidateForServerEvent(value, [["tasks"]]);
  await vi.advanceTimersByTimeAsync(10);
  const second = watch(value);
  await vi.advanceTimersByTimeAsync(10);
  expect(second.observer.getCurrentResult().error).toBeNull();
  await vi.advanceTimersByTimeAsync(1000);
  await next;
  expect(first.observer.getCurrentResult().data).toBe(2);
  expect(second.observer.getCurrentResult().data).toBe(2);
  expect(server.starts).toHaveLength(3);
});

it("resumes a canceled event only once without another cause", async () => {
  const value = client();
  const first = watch(value);
  await vi.advanceTimersByTimeAsync(160);
  server.version = 1;
  const changed = invalidateForServerEvent(value, [["tasks"]]).then(
    () => null,
    (failure: unknown) => failure,
  );
  await vi.advanceTimersByTimeAsync(10);
  first.stop();
  await vi.advanceTimersByTimeAsync(10);
  await changed;
  const second = watch(value);
  await vi.advanceTimersByTimeAsync(500);
  expect(server.active).toBe(1);
  second.stop();
  await vi.advanceTimersByTimeAsync(10);
  const third = watch(value);
  await vi.advanceTimersByTimeAsync(1000);
  expect(server.starts).toHaveLength(3);
  expect(third.observer.getCurrentResult().error?.name).toBe("CycleStopped");
});

it("follows a replacement of an event read started by a mount", async () => {
  const value = client();
  const first = watch(value);
  await vi.advanceTimersByTimeAsync(160);
  first.stop();
  server.version = 1;
  await invalidateForServerEvent(value, [["tasks"]]);
  const mounted = watch(value);
  const changed = invalidateForServerEvent(value, [["tasks"]]).then(
    () => null,
    (failure: unknown) => failure,
  );
  await vi.advanceTimersByTimeAsync(10);
  server.version = 2;
  window.dispatchEvent(new Event("click"));
  const replacement = mounted.observer.refetch();
  await vi.advanceTimersByTimeAsync(1000);
  await replacement;
  expect(await changed).toBeNull();
  expect(mounted.observer.getCurrentResult().data).toBe(2);
});
