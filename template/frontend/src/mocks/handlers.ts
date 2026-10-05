import { acceptsClientEvents } from "@/mocks/client-events";
import { http } from "@/mocks/http";
import { taskHandlers } from "@/mocks/tasks";

export const handlers = [
  ...taskHandlers,

  http.post("/client-events", async ({ request, response }) =>
    acceptsClientEvents(await request.json())
      ? response(204).empty()
      : response(422).json({ detail: [{ loc: ["body"], msg: "refused", type: "value_error" }] }),
  ),
];
