import { API_BASE_URL } from "@/api/base";
import type { CreateTaskBody, Task, UpdateTaskBody } from "@/api/types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? "GET";
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { "content-type": "application/json" },
  });
  if (!response.ok) {
    throw new Error(`${method} ${path} failed with ${response.status}`);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const tasksApi = {
  list: () => request<Task[]>("/tasks"),
  create: (body: CreateTaskBody) =>
    request<Task>("/tasks", { method: "POST", body: JSON.stringify(body) }),
  update: (id: string, body: UpdateTaskBody) =>
    request<Task>(`/tasks/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  remove: (id: string) => request<void>(`/tasks/${id}`, { method: "DELETE" }),
};
