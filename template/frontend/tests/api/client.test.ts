import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_BASE_URL } from "@/api/base";
import { ApiError, tasksApi } from "@/api/client";
import { server } from "@/mocks/node";

const REQUEST_ID = "0f8c2c1b9d2e4b6f8a1c3e5d7f9b0a2c";

describe("the api client", () => {
  it("gives a refused request the status and request id the backend answered with", async () => {
    server.use(
      http.delete(
        `${API_BASE_URL}/tasks/:id`,
        () =>
          new HttpResponse(JSON.stringify({ detail: "Task not found" }), {
            status: 404,
            headers: { "content-type": "application/json", "x-request-id": REQUEST_ID },
          }),
      ),
    );

    const refused = tasksApi.remove("does-not-exist");

    await expect(refused).rejects.toBeInstanceOf(ApiError);
    await expect(refused).rejects.toMatchObject({
      status: 404,
      requestId: REQUEST_ID,
      message: "Task not found",
    });
  });
});
