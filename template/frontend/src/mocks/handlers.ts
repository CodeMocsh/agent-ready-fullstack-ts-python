import { createOpenApiHttp } from "openapi-msw";
import { API_BASE_URL } from "@/api/base";
import type { paths } from "@/api/schema";
import { taskStore } from "@/mocks/store";

const http = createOpenApiHttp<paths>({ baseUrl: API_BASE_URL });

export const handlers = [
  http.get("/tasks", ({ response }) => response(200).json(taskStore.list())),

  http.post("/tasks", async ({ request, response }) => {
    const body = await request.json();
    return response(201).json(taskStore.create(body));
  }),

  http.patch("/tasks/{id}", async ({ params, request, response }) => {
    const body = await request.json();
    const updated = taskStore.update(params.id, body);
    return updated === null
      ? response(404).json({ detail: "Task not found" })
      : response(200).json(updated);
  }),

  http.delete("/tasks/{id}", ({ params, response }) =>
    taskStore.remove(params.id)
      ? response(204).empty()
      : response(404).json({ detail: "Task not found" }),
  ),
];
