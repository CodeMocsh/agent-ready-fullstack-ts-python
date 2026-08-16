import { afterEach, describe, expect, it } from "vitest";
import { tasksApi } from "@/api/client";

// One suite, two implementations of the same contract. `pnpm test:contract` runs it
// against the mock handlers; `make test-contract` runs it again with the backend
// half behind the dev server's /api proxy. Assertions describe the contract only --
// shapes and status codes, never seed data, which the two implementations are free
// to differ on. tests/setup.ts decides which implementation answers.
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

  it("reports a missing task as an error on update and remove", async () => {
    await expect(tasksApi.update("does-not-exist", { done: true })).rejects.toThrow("404");
    await expect(tasksApi.remove("does-not-exist")).rejects.toThrow("404");
  });
});
