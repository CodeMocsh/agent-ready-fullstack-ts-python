import { request } from "@/api/client";
import type { Job } from "@/api/jobs";
import type { components } from "@/api/schema";

export type Task = components["schemas"]["Task"];
export type TaskPage = components["schemas"]["TaskPage"];
export type CreateTaskBody = components["schemas"]["CreateTaskBody"];
export type UpdateTaskBody = components["schemas"]["UpdateTaskBody"];

export const tasksApi = {
  list: (after: string | null, signal: AbortSignal | null = null) =>
    request<TaskPage>(after === null ? "/tasks" : `/tasks?after=${encodeURIComponent(after)}`, {
      signal,
    }),
  create: (body: CreateTaskBody) =>
    request<Task>("/tasks", { method: "POST", body: JSON.stringify(body) }),
  update: (id: string, body: UpdateTaskBody) =>
    request<Task>(`/tasks/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  remove: (id: string) => request<void>(`/tasks/${id}`, { method: "DELETE" }),
  close: (id: string) => request<Job>(`/tasks/${id}/close`, { method: "POST" }),
};
