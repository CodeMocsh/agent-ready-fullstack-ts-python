import { API_BASE_URL } from "@/api/base";
import type { ClientEvents, CreateTaskBody, Task, UpdateTaskBody } from "@/api/types";

export class ApiError extends Error {
  override readonly name = "ApiError";
  readonly status: number;
  readonly requestId: string | null;

  constructor(message: string, status: number, requestId: string | null) {
    super(message);
    this.status = status;
    this.requestId = requestId;
  }
}

async function detailOf(response: Response): Promise<string | null> {
  const body: unknown = await response.json().catch(() => null);
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    return typeof detail === "string" && detail !== "" ? detail : null;
  }
  return null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? "GET";
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { "content-type": "application/json" },
  });
  if (!response.ok) {
    const detail = await detailOf(response);
    throw new ApiError(
      detail ?? `${method} ${path} failed with ${response.status}`,
      response.status,
      response.headers.get("x-request-id"),
    );
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

export const clientEventsApi = {
  record: (body: ClientEvents) =>
    request<void>("/client-events", {
      method: "POST",
      body: JSON.stringify(body),
      keepalive: true,
    }),
};
