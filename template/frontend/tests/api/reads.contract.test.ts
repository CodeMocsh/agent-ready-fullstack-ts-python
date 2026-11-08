import { describe, expect, it } from "vitest";
import { API_BASE_URL } from "@/api/base";
import { againstLiveBackend } from "./target";

type Part =
  | { kind: "answered"; status: number; headers: Record<string, string>; body: string }
  | { kind: "left"; reason: string };

async function reads(...paths: string[]): Promise<{ status: number; parts: Part[] }> {
  const query = paths.map((path) => `part=${encodeURIComponent(path)}`).join("&");
  const response = await fetch(`${API_BASE_URL}/reads?${query}`);
  const { parts } = (await response.json()) as { parts: Part[] };
  return { status: response.status, parts };
}

function bodyOf(part: Part | undefined): unknown {
  if (part?.kind !== "answered") {
    throw new Error(`expected an answered part, got ${JSON.stringify(part)}`);
  }
  return JSON.parse(part.body);
}

describe(`reads contract (${againstLiveBackend ? "live backend" : "mock handlers"})`, () => {
  it("answers each part in the order of the paths, as each route answers it alone", async () => {
    const { status, parts } = await reads(
      "/jobs/no-such-job",
      "/tasks?after=no-such-task",
      "/tasks",
    );

    expect(status).toBe(200);
    expect(parts.map((part) => part.kind === "answered" && part.status)).toEqual([404, 404, 200]);
    expect(bodyOf(parts[0])).toEqual({ detail: "Job not found" });
    expect(bodyOf(parts[1])).toEqual({ detail: "Task not found" });
    expect(bodyOf(parts[2])).toMatchObject({ tasks: expect.any(Array) });
  });

  it("answers a path that is not a part as a part that left its group", async () => {
    const { parts } = await reads("/reads?part=/tasks");

    expect(parts).toEqual([{ kind: "left", reason: "not a part" }]);
  });
});
