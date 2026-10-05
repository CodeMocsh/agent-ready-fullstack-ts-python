import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_BASE_URL } from "@/api/base";
import { ApiError, clientEventsApi } from "@/api/client";
import { server } from "@/mocks/node";

const REQUEST_ID = "0f8c2c1b9d2e4b6f8a1c3e5d7f9b0a2c";

describe("the api client", () => {
  it("gives a refused request the status and request id the backend answered with, and says what was refused when the detail is not a sentence", async () => {
    server.use(
      http.post(
        `${API_BASE_URL}/client-events`,
        () =>
          new HttpResponse(
            JSON.stringify({ detail: [{ loc: ["body"], msg: "refused", type: "value_error" }] }),
            {
              status: 422,
              headers: { "content-type": "application/json", "x-request-id": REQUEST_ID },
            },
          ),
      ),
    );

    const refused = clientEventsApi.record({ events: [] });

    await expect(refused).rejects.toBeInstanceOf(ApiError);
    await expect(refused).rejects.toMatchObject({
      status: 422,
      requestId: REQUEST_ID,
      message: "POST /client-events failed with 422",
    });
  });
});
