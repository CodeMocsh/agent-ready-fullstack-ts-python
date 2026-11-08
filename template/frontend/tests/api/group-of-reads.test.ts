import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API_BASE_URL } from "@/api/base";
import { ApiError, Held, request, Unreached } from "@/api/client";
import { PATHS_AT_MOST } from "@/api/parts";
import { server } from "@/mocks/node";

let day = 0;

function sentRequests(): () => string[] {
  const sent: string[] = [];
  const started = ({ request: started }: { request: Request }) => {
    const url = new URL(started.url);
    sent.push(`${url.pathname}${decodeURIComponent(url.search)}`);
  };
  server.events.on("request:start", started);
  return () => {
    server.events.removeListener("request:start", started);
    return sent;
  };
}

function readsAnswering(parts: unknown[]): void {
  server.use(http.get(`${API_BASE_URL}/reads`, () => HttpResponse.json({ parts })));
}

function nextTask(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 1));
}

const TOO_MANY = {
  kind: "answered",
  status: 429,
  headers: { "content-type": "application/json", "retry-after": "30" },
  body: JSON.stringify({ detail: "Too many requests" }),
};

function onADayOfItsOwn(): void {
  day += 1;
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(Date.UTC(2000, 6, day));
}

async function pastEveryWait(): Promise<void> {
  vi.setSystemTime(Date.now() + 3_600_000);
  await request("/tasks");
  vi.useRealTimers();
}

describe("the reads that start in one task", () => {
  beforeEach(onADayOfItsOwn);
  afterEach(pastEveryWait);

  it("go out as one GET /reads, and each call gets its own answer", async () => {
    const sent = sentRequests();

    const [tasks, job] = await Promise.allSettled([request("/tasks"), request("/jobs/none")]);

    expect(sent()).toEqual(["/api/reads?part=/tasks&part=/jobs/none"]);
    expect(tasks).toMatchObject({ status: "fulfilled", value: { next: null } });
    expect(job).toMatchObject({
      status: "rejected",
      reason: { name: "ApiError", status: 404, message: "Job not found" },
    });
  });

  it("go out alone when the task starts only one", async () => {
    const sent = sentRequests();

    await request("/tasks");

    expect(sent()).toEqual(["/api/tasks"]);
  });

  it("are one request per task", async () => {
    const sent = sentRequests();

    const first = request("/tasks");
    await nextTask();
    const second = request("/jobs/none");
    await Promise.allSettled([first, second]);

    expect(sent()).toEqual(["/api/tasks", "/api/jobs/none"]);
  });

  it("start a second group past the paths one group may name", async () => {
    const sent = sentRequests();
    const ids = Array.from({ length: PATHS_AT_MOST + 1 }, (_, id) => String(id));

    await Promise.allSettled(ids.map((id) => request(`/jobs/${id}`)));

    const requests = sent();
    expect(requests).toHaveLength(2);
    expect(requests[0]?.split("&part=")).toHaveLength(PATHS_AT_MOST);
    expect(requests[1]).toBe(`/api/jobs/${PATHS_AT_MOST}`);
  });

  it("send one path once, and answer every call of it", async () => {
    const sent = sentRequests();

    const answers = await Promise.all([request("/tasks"), request("/tasks")]);

    expect(sent()).toEqual(["/api/tasks"]);
    expect(answers[0]).toEqual(answers[1]);
  });

  it("send a path that is not a part alone, beside the group", async () => {
    server.use(http.get(`${API_BASE_URL}/echo`, () => HttpResponse.json({ echoed: true })));
    const sent = sentRequests();

    await Promise.all([request("/echo"), request("/tasks")]);

    expect(sent()).toEqual(["/api/echo", "/api/tasks"]);
  });
});

describe("a failure of /reads", () => {
  beforeEach(onADayOfItsOwn);
  afterEach(pastEveryWait);

  it("give the refusal of /reads itself to every part", async () => {
    server.use(
      http.get(`${API_BASE_URL}/reads`, () =>
        HttpResponse.json({ detail: "Refused" }, { status: 401 }),
      ),
    );

    const answers = await Promise.allSettled([request("/tasks"), request("/jobs/1")]);

    expect(answers).toMatchObject([
      { status: "rejected", reason: { name: "ApiError", status: 401, message: "Refused" } },
      { status: "rejected", reason: { name: "ApiError", status: 401, message: "Refused" } },
    ]);
  });

  it("fail as Unreached, each naming its own path, when /reads never reaches the server", async () => {
    server.use(http.get(`${API_BASE_URL}/reads`, () => HttpResponse.error()));

    const answers = await Promise.allSettled([request("/tasks"), request("/jobs/1")]);

    expect(answers).toMatchObject([
      { status: "rejected", reason: { message: "GET /tasks did not reach the server" } },
      { status: "rejected", reason: { message: "GET /jobs/1 did not reach the server" } },
    ]);
    for (const answer of answers) {
      expect(answer.status === "rejected" && answer.reason).toBeInstanceOf(Unreached);
    }
  });
});

describe("a part that left its group", () => {
  beforeEach(onADayOfItsOwn);
  afterEach(pastEveryWait);

  it("send a part that left its group again alone, once", async () => {
    let alone = 0;
    server.use(
      http.get(`${API_BASE_URL}/jobs/slow`, () => {
        alone += 1;
        return HttpResponse.json({ detail: "Job not found" }, { status: 404 });
      }),
    );
    readsAnswering([
      { kind: "answered", status: 200, headers: {}, body: '{"tasks":[],"next":null}' },
      { kind: "left", reason: "timed out" },
    ]);

    const [, job] = await Promise.allSettled([request("/tasks"), request("/jobs/slow")]);

    expect(alone).toBe(1);
    expect(job).toMatchObject({ status: "rejected", reason: { status: 404 } });
  });

  it("send no part that left its group while a part of it asks every request to wait", async () => {
    let alone = 0;
    server.use(
      http.get(`${API_BASE_URL}/jobs/slow`, () => {
        alone += 1;
        return HttpResponse.json({});
      }),
    );
    readsAnswering([TOO_MANY, { kind: "left", reason: "timed out" }]);

    const answers = await Promise.allSettled([request("/tasks"), request("/jobs/slow")]);

    expect(alone).toBe(0);
    expect(answers).toMatchObject([
      { status: "rejected", reason: { status: 429, retryAfterSeconds: 30 } },
      { status: "rejected", reason: { status: 429, retryAfterSeconds: 30 } },
    ]);
  });

  it("hold every later request after a part's Retry-After", async () => {
    readsAnswering([TOO_MANY, { kind: "answered", status: 200, headers: {}, body: "{}" }]);

    const answers = await Promise.allSettled([request("/tasks"), request("/jobs/1")]);
    const later = request("/tasks");

    expect(answers[0]).toMatchObject({ status: "rejected", reason: { status: 429 } });
    expect(answers[1]).toMatchObject({ status: "fulfilled", value: {} });
    await expect(later).rejects.toBeInstanceOf(Held);
    await expect(later).rejects.toBeInstanceOf(ApiError);
  });

  it("refuse an answer that does not hold one part for each path", async () => {
    readsAnswering([{ kind: "answered", status: 200, headers: {}, body: "{}" }]);

    const answers = await Promise.allSettled([request("/tasks"), request("/jobs/1")]);

    expect(answers).toMatchObject([
      { status: "rejected", reason: { message: "GET /reads answered 1 parts for 2 paths" } },
      { status: "rejected", reason: { message: "GET /reads answered 1 parts for 2 paths" } },
    ]);
  });
});

describe("a part the group cannot answer", () => {
  beforeEach(onADayOfItsOwn);
  afterEach(pastEveryWait);

  it("refuse a part of no known kind", async () => {
    readsAnswering([
      { kind: "answered", status: 200, headers: {}, body: "{}" },
      { kind: "unknown" },
    ]);

    const answers = await Promise.allSettled([request("/tasks"), request("/jobs/1")]);

    expect(answers).toMatchObject([
      { status: "fulfilled", value: {} },
      {
        status: "rejected",
        reason: { message: "GET /reads answered /jobs/1 with a part of no known kind" },
      },
    ]);
  });

  it("fail as Unreached when a part alone never reaches the server", async () => {
    server.use(http.get(`${API_BASE_URL}/jobs/gone`, () => HttpResponse.error()));

    const answers = await Promise.allSettled([request("/tasks"), request("/jobs/gone")]);

    expect(answers[0]).toMatchObject({ status: "fulfilled" });
    expect(answers[1].status === "rejected" && answers[1].reason).toBeInstanceOf(Unreached);
  });
});
