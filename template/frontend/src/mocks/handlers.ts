import { acceptsClientEvents } from "@/mocks/client-events";
import { http } from "@/mocks/http";
import { jobHandlers, jobStore } from "@/mocks/jobs";
import { PAGE_MAX, pageAtMost } from "@/mocks/store";
import { resetTasks, taskHandlers } from "@/mocks/tasks";

export const handlers = [
  ...taskHandlers,
  ...jobHandlers,

  http.post("/client-events", async ({ request, response }) =>
    acceptsClientEvents(await request.json())
      ? response(204).empty()
      : response(422).json({ detail: [{ loc: ["body"], msg: "refused", type: "value_error" }] }),
  ),
];

export function resetMockState(): void {
  pageAtMost(PAGE_MAX);
  resetTasks();
  jobStore.reset();
}
