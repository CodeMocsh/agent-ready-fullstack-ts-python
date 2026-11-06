import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API_BASE_URL } from "@/api/base";
import { ApiError, clientEventsApi, Held, request, Unreached } from "@/api/client";
import { server } from "@/mocks/node";

const REQUEST_ID = "0f8c2c1b9d2e4b6f8a1c3e5d7f9b0a2c";
const LONG_DAYS: Record<string, string> = {
  Mon: "Monday",
  Tue: "Tuesday",
  Wed: "Wednesday",
  Thu: "Thursday",
  Fri: "Friday",
  Sat: "Saturday",
  Sun: "Sunday",
};

function refusedWithRetryAfter(path: string, status: number, retryAfter: string): () => number {
  let reached = 0;
  server.use(
    http.get(`${API_BASE_URL}${path}`, () => {
      reached += 1;
      return HttpResponse.json(
        { detail: "Too many requests" },
        { status, headers: { "retry-after": retryAfter, "x-request-id": REQUEST_ID } },
      );
    }),
  );
  return () => reached;
}

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

  it("sends the headers a caller passes, alongside its own content type", async () => {
    const sent: Headers[] = [];
    server.use(
      http.get(`${API_BASE_URL}/echo`, ({ request: received }) => {
        sent.push(received.headers);
        return HttpResponse.json({});
      }),
    );

    await request("/echo", { headers: { "x-caller": "kept" } });

    expect(sent.map((headers) => [headers.get("x-caller"), headers.get("content-type")])).toEqual([
      ["kept", "application/json"],
    ]);
  });
});

let day = 0;

function onADayOfItsOwn(): void {
  day += 1;
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(Date.UTC(2000, 0, day));
}

describe("a wait the server asked for", () => {
  beforeEach(onADayOfItsOwn);

  afterEach(() => {
    vi.useRealTimers();
  });

  it.each([
    [429, "/held-429"],
    [503, "/held-503"],
  ])(
    "holds every request to the API after a %i with Retry-After, without sending it, until the wait ends",
    async (status, path) => {
      const reached = refusedWithRetryAfter(path, status, "2");

      await expect(request(path)).rejects.toMatchObject({ status, retryAfterSeconds: 2 });
      const held = request("/elsewhere", { method: "POST" });
      await expect(held).rejects.toBeInstanceOf(Held);
      await expect(held).rejects.toMatchObject({
        status,
        requestId: null,
        message: `POST /elsewhere was not sent: the server answered ${status} and asked every request to wait 2 s more`,
      });
      expect(reached()).toBe(1);

      vi.setSystemTime(Date.now() + 2000);
      await expect(request(path)).rejects.not.toBeInstanceOf(Held);
      expect(reached()).toBe(2);
    },
  );

  it("sends a client event while every other request waits", async () => {
    refusedWithRetryAfter("/held-events", 429, "60");
    await expect(request("/held-events")).rejects.toBeInstanceOf(ApiError);

    await expect(
      clientEventsApi.record({
        events: [{ kind: "query", route: null, error: "Held", status: 429, request_id: null }],
      }),
    ).resolves.toBeUndefined();
  });
});

describe("a Retry-After the client reads", () => {
  beforeEach(onADayOfItsOwn);

  afterEach(() => {
    vi.useRealTimers();
  });

  it.each([
    ["IMF-fixdate", (at: Date) => at.toUTCString()],
    [
      "RFC 850",
      (at: Date) => {
        const [day, rest] = at.toUTCString().split(", ") as [string, string];
        const [date, month, year, time] = rest.split(" ") as [string, string, string, string];
        return `${LONG_DAYS[day]}, ${date}-${month}-${year.slice(2)} ${time} GMT`;
      },
    ],
    [
      "asctime",
      (at: Date) => {
        const [day, rest] = at.toUTCString().split(", ") as [string, string];
        const [date, month, year, time] = rest.split(" ") as [string, string, string, string];
        return `${day} ${month} ${String(Number(date)).padStart(2, " ")} ${time} ${year}`;
      },
    ],
  ])("reads a Retry-After that names an HTTP date in the %s form", async (_form, written) => {
    refusedWithRetryAfter("/held-until", 503, written(new Date(Date.now() + 30_000)));

    await expect(request("/held-until")).rejects.toMatchObject({ retryAfterSeconds: 30 });
  });

  it.each([
    "soon",
    "2000-01-01T00:00:30Z",
    "1.5",
    "Mon, 32 Jan 2000 00:00:00 GMT",
    "Sat, 01 Jan 2000 99:99:99 GMT",
  ])("refuses a Retry-After of %s as the refusal it came with, and holds nothing", async (said) => {
    refusedWithRetryAfter("/held-badly", 429, said);
    server.use(http.get(`${API_BASE_URL}/not-held`, () => HttpResponse.json({ ok: true })));

    const refused = request("/held-badly");
    await expect(refused).rejects.toBeInstanceOf(ApiError);
    await expect(refused).rejects.toMatchObject({
      status: 429,
      message: `Too many requests. Its Retry-After "${said}" is neither a number of seconds nor an HTTP date`,
    });
    await expect(request("/not-held")).resolves.toEqual({ ok: true });
  });
});

describe("the length of a wait", () => {
  beforeEach(onADayOfItsOwn);

  afterEach(() => {
    vi.useRealTimers();
  });

  it("keeps the longer wait when a later refusal asks for a shorter one", async () => {
    refusedWithRetryAfter("/held-long", 429, "60");
    refusedWithRetryAfter("/held-short", 503, "2");
    await expect(request("/held-long")).rejects.toMatchObject({ retryAfterSeconds: 60 });
    await expect(request("/held-short", undefined, { whileHeld: "sends" })).rejects.toMatchObject({
      retryAfterSeconds: 2,
    });

    vi.setSystemTime(Date.now() + 2000);

    await expect(request("/held-short")).rejects.toBeInstanceOf(Held);
  });

  it("holds nothing for a refusal other than 429 or 503 that names a wait", async () => {
    refusedWithRetryAfter("/moved", 500, "60");
    server.use(http.get(`${API_BASE_URL}/not-held`, () => HttpResponse.json({ ok: true })));

    await expect(request("/moved")).rejects.toMatchObject({ status: 500, retryAfterSeconds: 60 });
    await expect(request("/not-held")).resolves.toEqual({ ok: true });
  });
});

describe("a request that never reaches the server", () => {
  it("fails as Unreached, which is the one failure a read retries", async () => {
    server.use(http.get(`${API_BASE_URL}/unreachable`, () => HttpResponse.error()));

    await expect(request("/unreachable")).rejects.toBeInstanceOf(Unreached);
  });
});
