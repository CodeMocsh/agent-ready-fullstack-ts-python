import { request } from "@/api/client";
import type { components } from "@/api/schema";

export type Task = components["schemas"]["Task"];
export type CreateTaskBody = components["schemas"]["CreateTaskBody"];
export type UpdateTaskBody = components["schemas"]["UpdateTaskBody"];

export const tasksApi = {
  list: () => request<Task[]>("/tasks"),
  create: (body: CreateTaskBody) =>
    request<Task>("/tasks", { method: "POST", body: JSON.stringify(body) }),
  update: (id: string, body: UpdateTaskBody) =>
    request<Task>(`/tasks/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  remove: (id: string) => request<void>(`/tasks/${id}`, { method: "DELETE" }),
};
