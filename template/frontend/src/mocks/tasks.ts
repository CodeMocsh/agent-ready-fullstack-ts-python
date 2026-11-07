import { http } from "@/mocks/http";
import { jobStore } from "@/mocks/jobs";
import { taskStore } from "@/mocks/store";

export function resetTasks(): void {
  taskStore.reset();
}

export const taskHandlers = [
  http.get("/tasks", ({ query, response }) => {
    const page = taskStore.list(query.get("after"));
    return page === null
      ? response(404).json({ detail: "Task not found" })
      : response(200).json(page);
  }),

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

  http.post("/tasks/{id}/close", ({ params, response }) =>
    response(202).json(
      jobStore.ended("close a task", {
        closed: taskStore.update(params.id, { done: true }) !== null,
      }),
    ),
  ),

  http.delete("/tasks/{id}", ({ params, response }) =>
    taskStore.remove(params.id)
      ? response(204).empty()
      : response(404).json({ detail: "Task not found" }),
  ),
];
