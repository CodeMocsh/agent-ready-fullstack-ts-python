import { API_BASE_URL } from "@/api/base";
import { NotAnHttpDate, timeOf } from "@/api/http-date";
import type { ClientEvents } from "@/api/types";

export class ApiError extends Error {
  override readonly name: string = "ApiError";
  readonly status: number;
  readonly requestId: string | null;
  readonly retryAfterSeconds: number | null;

  constructor(
    message: string,
    status: number,
    requestId: string | null,
    retryAfterSeconds: number | null,
  ) {
    super(message);
    this.status = status;
    this.requestId = requestId;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

export class Held extends ApiError {
  override readonly name: string = "Held";
}

export class Unreached extends Error {
  override readonly name = "Unreached";
}

export type Sending = { whileHeld: "waits" | "sends" };

type Hold = { until: number; status: number };

const WAITS: Sending = { whileHeld: "waits" };
const HOLDING = new Set([429, 503]);

let hold: Hold | null = null;

async function detailOf(response: Response): Promise<string | null> {
  const body: unknown = await response.json().catch(() => null);
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    return typeof detail === "string" && detail !== "" ? detail : null;
  }
  return null;
}

function secondsUntil(said: string): number {
  const trimmed = said.trim();
  if (/^\d+$/.test(trimmed)) {
    return Number(trimmed);
  }
  return Math.max(0, Math.ceil((timeOf(trimmed, Date.now()) - Date.now()) / 1000));
}

function retryAfterSecondsOf(response: Response, refused: string): number | null {
  const said = response.headers.get("retry-after");
  if (said === null) {
    return null;
  }
  try {
    return secondsUntil(said);
  } catch (failure: unknown) {
    if (!(failure instanceof NotAnHttpDate)) {
      throw failure;
    }
    throw new ApiError(
      `${refused}. Its Retry-After "${said}" is neither a number of seconds nor an HTTP date`,
      response.status,
      response.headers.get("x-request-id"),
      null,
    );
  }
}

function holdFor(seconds: number, status: number): void {
  const until = Date.now() + seconds * 1000;
  if (hold === null || hold.until < until) {
    hold = { until, status };
  }
}

function refuseWhileHeld(asked: string): void {
  if (hold === null) {
    return;
  }
  const left = Math.ceil((hold.until - Date.now()) / 1000);
  if (left <= 0) {
    hold = null;
    return;
  }
  throw new Held(
    `${asked} was not sent: the server answered ${hold.status} and asked every request to wait ${left} s more`,
    hold.status,
    null,
    left,
  );
}

async function sent(url: string, init: RequestInit, asked: string): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch (failure: unknown) {
    throw new Unreached(`${asked} did not reach the server`, { cause: failure });
  }
}

export async function request<T>(
  path: string,
  init?: RequestInit,
  sending: Sending = WAITS,
): Promise<T> {
  const method = init?.method ?? "GET";
  const asked = `${method} ${path}`;
  if (sending.whileHeld === "waits") {
    refuseWhileHeld(asked);
  }
  const response = await sent(
    `${API_BASE_URL}${path}`,
    {
      ...init,
      headers: {
        "content-type": "application/json",
        ...Object.fromEntries(new Headers(init?.headers)),
      },
    },
    asked,
  );
  if (!response.ok) {
    const refused = (await detailOf(response)) ?? `${asked} failed with ${response.status}`;
    const retryAfterSeconds = retryAfterSecondsOf(response, refused);
    if (retryAfterSeconds !== null && HOLDING.has(response.status)) {
      holdFor(retryAfterSeconds, response.status);
    }
    throw new ApiError(
      refused,
      response.status,
      response.headers.get("x-request-id"),
      retryAfterSeconds,
    );
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const clientEventsApi = {
  record: (body: ClientEvents) =>
    request<void>(
      "/client-events",
      { method: "POST", body: JSON.stringify(body), keepalive: true },
      { whileHeld: "sends" },
    ),
};
