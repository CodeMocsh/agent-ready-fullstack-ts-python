import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API_BASE_URL } from "@/api/base";
import { ApiError } from "@/api/client";
import type { ClientEvents } from "@/api/types";
import { type RecordClientEvent, startClientEvents } from "@/client-events";
import { server } from "@/mocks/node";
import { router } from "@/router";

const CANARY = "canary-6f1e2d-alice@example.com";
const REQUEST_ID = "0f8c2c1b9d2e4b6f8a1c3e5d7f9b0a2c";

function capturePosts(): ClientEvents[] {
  const posted: ClientEvents[] = [];
  server.use(
    http.post(`${API_BASE_URL}/client-events`, async ({ request }) => {
      posted.push((await request.json()) as ClientEvents);
      return new HttpResponse(null, { status: 204 });
    }),
  );
  return posted;
}

async function settled(posted: ClientEvents[], count: number): Promise<void> {
  await expect.poll(() => posted.length).toBe(count);
}

describe("client events", () => {
  let record: RecordClientEvent;

  beforeEach(async () => {
    await router.load();
    record = startClientEvents();
  });

  it("reports a failed request by its route template, its status and the id it answered with", async () => {
    const posted = capturePosts();

    record("query", new ApiError("Task not found", 404, REQUEST_ID));

    await settled(posted, 1);
    expect(posted[0]).toEqual({
      events: [
        { kind: "query", route: "/", error: "ApiError", status: 404, request_id: REQUEST_ID },
      ],
    });
  });

  it("never sends what an error says, only what it is", async () => {
    const posted = capturePosts();

    record("uncaught", new TypeError(CANARY));

    await settled(posted, 1);
    expect(posted[0]?.events[0]?.error).toBe("TypeError");
    expect(JSON.stringify(posted)).not.toContain(CANARY);
  });

  it("reports a thrown value that is not an error by its type", async () => {
    const posted = capturePosts();

    record("uncaught", CANARY);

    await settled(posted, 1);
    expect(posted[0]?.events[0]).toMatchObject({ error: "string", status: null, request_id: null });
  });

  it("sends the same failure once per page", async () => {
    const posted = capturePosts();

    record("mutation", new ApiError("Task not found", 404, REQUEST_ID));
    record("mutation", new ApiError("Task not found", 404, REQUEST_ID));
    record("query", new ApiError("Task not found", 404, REQUEST_ID));

    await settled(posted, 2);
  });

  it("sends at most one page's worth of distinct failures", async () => {
    const posted = capturePosts();

    for (let status = 400; status < 430; status += 1) {
      record("query", new ApiError("refused", status, REQUEST_ID));
    }

    await settled(posted, 20);
  });

  it("says in the console, and nowhere else, that a client event could not be delivered", async () => {
    const refusals: unknown[] = [];
    server.use(
      http.post(`${API_BASE_URL}/client-events`, () => {
        refusals.push(null);
        return HttpResponse.json({ detail: "refused" }, { status: 422 });
      }),
    );
    const said = vi.spyOn(console, "error").mockImplementation(() => undefined);

    record("uncaught", new TypeError(CANARY));

    await expect.poll(() => said.mock.calls.length).toBe(1);
    expect(refusals).toHaveLength(1);
    said.mockRestore();
  });
});
