import { afterEach, describe, expect, it } from "vitest";
import { API_BASE_URL } from "@/api/base";
import { type Job, jobsApi } from "@/api/jobs";
import { type Task, tasksApi } from "@/api/tasks";
import { againstLiveBackend } from "./target";

async function ended(id: string): Promise<Job> {
  const deadline = Date.now() + 15_000;
  for (;;) {
    const job = await jobsApi.read(id);
    if (job.state !== "pending" || Date.now() > deadline) {
      return job;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
}

async function everyTask(): Promise<Task[]> {
  const tasks: Task[] = [];
  let after: string | null = null;
  do {
    const page = await tasksApi.list(after);
    tasks.push(...page.tasks);
    after = page.next;
  } while (after !== null);
  return tasks;
}

describe(`tasks contract (${againstLiveBackend ? "live backend" : "mock handlers"})`, () => {
  if (againstLiveBackend) {
    afterEach(async () => {
      for (const task of await everyTask()) {
        await tasksApi.remove(task.id);
      }
    });
  }

  it("lists a page of tasks", async () => {
    const page = await tasksApi.list(null);
    expect(Array.isArray(page.tasks)).toBe(true);
    expect(page.next === null || typeof page.next === "string").toBe(true);
    for (const task of page.tasks) {
      expect(typeof task.id).toBe("string");
      expect(typeof task.title).toBe("string");
      expect(typeof task.done).toBe("boolean");
    }
  });

  it("creates a task that is not done and appears in the list", async () => {
    const created = await tasksApi.create({ title: "Prove the contract" });
    expect(created.title).toBe("Prove the contract");
    expect(created.done).toBe(false);
    expect((await everyTask()).some((task) => task.id === created.id)).toBe(true);
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
    expect((await everyTask()).some((task) => task.id === created.id)).toBe(false);
  });

  it("lists the newest task first", async () => {
    await tasksApi.create({ title: "Older" });
    const newest = await tasksApi.create({ title: "Newer" });
    expect((await tasksApi.list(null)).tasks[0]).toEqual(newest);
  });

  it("starts the next page after the task it names", async () => {
    const older = await tasksApi.create({ title: "Older of two" });
    const newer = await tasksApi.create({ title: "Newer of two" });
    const page = await tasksApi.list(newer.id);
    expect(page.tasks[0]?.id).toBe(older.id);
  });

  it("refuses a page after a task that is not there with a 404 and an ErrorBody", async () => {
    const answered = await fetch(`${API_BASE_URL}/tasks?after=does-not-exist`);
    expect(answered.status).toBe(404);
    expect(await answered.json()).toEqual({ detail: "Task not found" });
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

describe(`jobs contract (${againstLiveBackend ? "live backend" : "mock handlers"})`, () => {
  it("answers a job it was asked for as pending, and reads it back ended", async () => {
    const asked = await tasksApi.close("does-not-exist");
    expect(asked.state).toBe("pending");
    expect(await ended(asked.id)).toEqual({
      id: asked.id,
      kind: "close a task",
      state: "done",
      outcome: { closed: false },
      failure: null,
    });
  });

  it("refuses a job that is not there with a 404 and an ErrorBody", async () => {
    const answered = await fetch(`${API_BASE_URL}/jobs/does-not-exist`);
    expect(answered.status).toBe(404);
    expect(await answered.json()).toEqual({ detail: "Job not found" });
  });
});

if (againstLiveBackend) {
  it("refuses an oversized page through the live HTTP path", async () => {
    const created: string[] = [];
    await new Promise((resolve) => setTimeout(resolve, 1000));
    try {
      for (let index = 0; index < 9; index += 1) {
        await new Promise((resolve) => setTimeout(resolve, 250));
        const task = await tasksApi.create({ title: "x".repeat(512 * 1024) });
        created.push(task.id);
      }
      const answered = await fetch(`${API_BASE_URL}/tasks`);
      expect(answered.status).toBe(500);
      expect(await answered.json()).toEqual({ detail: "Response exceeds its byte limit" });
      await expect(tasksApi.list(null)).rejects.toThrow("Response exceeds its byte limit");
    } finally {
      for (const id of created) {
        await new Promise((resolve) => setTimeout(resolve, 250));
        await tasksApi.remove(id);
      }
    }
    expect((await tasksApi.list(null)).tasks.every((task) => !created.includes(task.id))).toBe(
      true,
    );
  });
}
