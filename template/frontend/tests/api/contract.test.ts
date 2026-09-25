import { afterEach, describe, expect, it } from "vitest";
import { API_BASE_URL } from "@/api/base";
import { clientEventsApi, tasksApi } from "@/api/client";

const againstLiveBackend = process.env.CONTRACT_TARGET === "live";

describe(`tasks contract (${againstLiveBackend ? "live backend" : "mock handlers"})`, () => {
  if (againstLiveBackend) {
    afterEach(async () => {
      for (const task of await tasksApi.list()) {
        await tasksApi.remove(task.id);
      }
    });
  }

  it("lists tasks", async () => {
    const tasks = await tasksApi.list();
    expect(Array.isArray(tasks)).toBe(true);
    for (const task of tasks) {
      expect(typeof task.id).toBe("string");
      expect(typeof task.title).toBe("string");
      expect(typeof task.done).toBe("boolean");
    }
  });

  it("creates a task that is not done and appears in the list", async () => {
    const created = await tasksApi.create({ title: "Prove the contract" });
    expect(created.title).toBe("Prove the contract");
    expect(created.done).toBe(false);
    expect((await tasksApi.list()).some((task) => task.id === created.id)).toBe(true);
  });

  it("updates a task's done flag", async () => {
    const created = await tasksApi.create({ title: "Toggle me" });
    const updated = await tasksApi.update(created.id, { done: true });
    expect(updated.id).toBe(created.id);
    expect(updated.done).toBe(true);
  });

  it("removes a task", async () => {
    const created = await tasksApi.create({ title: "Delete me" });
    await tasksApi.remove(created.id);
    expect((await tasksApi.list()).some((task) => task.id === created.id)).toBe(false);
  });

  it("refuses a missing task with a 404 and an ErrorBody", async () => {
    const answered = await fetch(`${API_BASE_URL}/tasks/does-not-exist`, { method: "DELETE" });
    expect(answered.status).toBe(404);
    expect(await answered.json()).toEqual({ detail: "Task not found" });
  });

  it("reports a missing task with the detail both implementations write", async () => {
    await expect(tasksApi.update("does-not-exist", { done: true })).rejects.toThrow(
      "Task not found",
    );
    await expect(tasksApi.remove("does-not-exist")).rejects.toThrow("Task not found");
  });
});

const DECLARED = {
  kind: "uncaught",
  route: "/",
  error: "TypeError",
  status: null,
  request_id: null,
};

function postClientEvent(event: Record<string, unknown>): Promise<Response> {
  return fetch(`${API_BASE_URL}/client-events`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ events: [event] }),
  });
}

describe(`client events contract (${againstLiveBackend ? "live backend" : "mock handlers"})`, () => {
  it("accepts a client event with no body in return", async () => {
    await expect(
      clientEventsApi.record({ events: [{ ...DECLARED, kind: "uncaught" }] }),
    ).resolves.toBeUndefined();
  });

  it("refuses a client event that carries free text", async () => {
    expect((await postClientEvent({ ...DECLARED, error: "a sentence" })).status).toBe(422);
  });

  it("refuses a client event with a field nobody declared", async () => {
    expect((await postClientEvent({ ...DECLARED, message: "anything" })).status).toBe(422);
  });
});
