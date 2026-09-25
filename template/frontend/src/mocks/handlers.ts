import { createOpenApiHttp } from "openapi-msw";
import { API_BASE_URL } from "@/api/base";
import type { paths } from "@/api/schema";
import type { ClientEvents } from "@/api/types";
import { taskStore } from "@/mocks/store";

const http = createOpenApiHttp<paths>({ baseUrl: API_BASE_URL });

const CLIENT_EVENT_FIELDS = new Set(["kind", "route", "error", "status", "request_id"]);
const ERROR_NAME = /^[A-Za-z][A-Za-z0-9_]*$/;
const MAX_CLIENT_EVENTS = 20;

function refusesClientEvents(body: ClientEvents): boolean {
  return (
    body.events.length === 0 ||
    body.events.length > MAX_CLIENT_EVENTS ||
    body.events.some(
      (event) =>
        Object.keys(event).some((field) => !CLIENT_EVENT_FIELDS.has(field)) ||
        !ERROR_NAME.test(event.error),
    )
  );
}

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

  http.post("/client-events", async ({ request, response }) =>
    refusesClientEvents(await request.json())
      ? response(422).json({
          detail: [{ loc: ["body", "events"], msg: "refused", type: "value_error" }],
        })
      : response(204).empty(),
  ),

  http.delete("/tasks/{id}", ({ params, response }) =>
    taskStore.remove(params.id)
      ? response(204).empty()
      : response(404).json({ detail: "Task not found" }),
  ),
];
