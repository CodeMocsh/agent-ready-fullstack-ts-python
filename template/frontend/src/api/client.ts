import { API_BASE_URL } from "@/api/base";
import { NotAnHttpDate, timeOf } from "@/api/http-date";
import { PARTS, PATHS_AT_MOST } from "@/api/parts";
import {
  copyReadBudget,
  type ReadBudget,
  readBudget,
  readSignal,
  waitForRead,
} from "@/api/read-budget";
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
    init.signal?.throwIfAborted();
    throw new Unreached(`${asked} did not reach the server`, { cause: failure });
  }
}

type Waiter = { resolve: (response: Response) => void; reject: (failure: unknown) => void };
type Consumers = { waiters: Set<Waiter>; controller: AbortController };

type AnsweredPart = {
  kind: "answered";
  status: number;
  headers: Record<string, string>;
  body: string;
};

type Part = AnsweredPart | { kind: "left"; reason: string };

const JSON_HEADERS = { "content-type": "application/json" };

const PART_PATTERNS = PARTS.map(patternOf);

type Group = {
  paths: Map<string, Consumers>;
  budget: ReadBudget | undefined;
  controller: AbortController;
  timer: ReturnType<typeof setTimeout>;
  sent: boolean;
};

const queued = new Map<ReadBudget | undefined, Group>();

function patternOf(template: string): RegExp {
  const segments = template
    .split("/")
    .map((segment) =>
      /^\{[^/{}]+\}$/.test(segment) ? "[^/]+" : segment.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"),
    );
  return new RegExp(`^${segments.join("/")}$`);
}

export function isPartPath(path: string): boolean {
  const bare = path.replace(/\?.*$/s, "");
  return PART_PATTERNS.some((pattern) => pattern.test(bare));
}

function isPart(path: string, init: RequestInit | undefined): boolean {
  const onlyGet =
    init === undefined ||
    Object.entries(init).every(
      ([name, value]) => name === "signal" || (name === "method" && value === "GET"),
    );
  return onlyGet && isPartPath(path);
}

function direct(path: string, init?: RequestInit): Promise<Response> {
  return sent(
    `${API_BASE_URL}${path}`,
    { ...init, headers: { ...JSON_HEADERS, ...Object.fromEntries(new Headers(init?.headers)) } },
    `${init?.method ?? "GET"} ${path}`,
  );
}

function started(budget: ReadBudget | undefined): Group {
  const group: Group = {
    paths: new Map(),
    budget,
    controller: new AbortController(),
    timer: setTimeout(() => sendOff(group), 0),
    sent: false,
  };
  queued.set(budget, group);
  return group;
}

function sendOff(group: Group): void {
  clearTimeout(group.timer);
  if (queued.get(group.budget) === group) queued.delete(group.budget);
  group.sent = true;
  if (group.paths.size > 0) void answer(group);
}

function leave(
  group: Group,
  path: string,
  consumers: Consumers,
  waiter: Waiter,
  canceled: boolean,
): void {
  consumers.waiters.delete(waiter);
  if (consumers.waiters.size > 0 || !canceled) return;
  consumers.controller.abort();
  if (!group.sent) group.paths.delete(path);
  if ([...group.paths.values()].every((part) => part.waiters.size === 0)) {
    group.controller.abort();
    if (!group.sent) {
      clearTimeout(group.timer);
      if (queued.get(group.budget) === group) queued.delete(group.budget);
    }
  }
}

function join<T>(group: Group, path: string, signal: AbortSignal | undefined): Promise<T> {
  let consumers = group.paths.get(path);
  if (consumers === undefined) {
    consumers = { waiters: new Set(), controller: new AbortController() };
    copyReadBudget(signal, consumers.controller.signal);
    group.paths.set(path, consumers);
  }
  const part = consumers;
  return new Promise((resolve, reject) => {
    const finish = (canceled = false) => {
      signal?.removeEventListener("abort", abort);
      leave(group, path, part, waiter, canceled);
    };
    const waiter: Waiter = {
      resolve: (response) => {
        answered<T>(response, `GET ${path}`).then(
          (value) => {
            finish();
            resolve(value);
          },
          (failure: unknown) => {
            finish();
            reject(failure);
          },
        );
      },
      reject: (failure) => {
        finish();
        reject(failure);
      },
    };
    const abort = () => {
      finish(true);
      reject(signal?.reason);
    };
    part.waiters.add(waiter);
    signal?.addEventListener("abort", abort, { once: true });
  });
}

function grouped<T>(path: string, signal: AbortSignal | undefined): Promise<T> {
  signal?.throwIfAborted();
  const budget = readBudget(signal);
  let group = queued.get(budget) ?? started(budget);
  if (!group.paths.has(path) && group.paths.size === PATHS_AT_MOST) {
    sendOff(group);
    group = started(budget);
  }
  return join<T>(group, path, signal);
}

function onlyOf(paths: string[]): string {
  const [only] = paths;
  if (only === undefined || paths.length !== 1) {
    throw new Error(`a group of ${paths.length} paths is not a group of one`);
  }
  return only;
}

async function answer(group: Group): Promise<void> {
  const paths = [...group.paths.keys()];
  let responses: Promise<Response>[];
  try {
    responses =
      paths.length === 1
        ? [direct(onlyOf(paths), { signal: group.controller.signal })]
        : await answersOf(paths, group);
  } catch (failure: unknown) {
    responses = paths.map((path) => Promise.reject(failureFor(path, failure)));
  }
  [...group.paths].forEach(([path, consumers], index) => {
    const response =
      responses[index] ?? Promise.reject(new Error(`the group answered nothing for ${path}`));
    response.then(
      (answered) => {
        const waiters = [...consumers.waiters];
        const last = waiters.length - 1;
        waiters.forEach((waiter, copy) => {
          waiter.resolve(copy === last ? answered : answered.clone());
        });
      },
      (failure: unknown) => {
        for (const waiter of [...consumers.waiters]) {
          waiter.reject(failure);
        }
      },
    );
  });
}

function failureFor(path: string, failure: unknown): unknown {
  return failure instanceof Unreached
    ? new Unreached(`GET ${path} did not reach the server`, { cause: failure.cause })
    : failure;
}

function responseOf(part: AnsweredPart): Response {
  return new Response(part.body === "" ? null : part.body, {
    status: part.status,
    headers: part.headers,
  });
}

async function alone(path: string, group: Group): Promise<Response> {
  const consumers = group.paths.get(path);
  if (consumers === undefined) throw new Error(`No consumers for ${path}`);
  const signal = consumers.controller.signal;
  await waitForRead(signal);
  refuseWhileHeld(`GET ${path}`);
  return direct(path, { signal });
}

async function answersOf(paths: string[], group: Group): Promise<Promise<Response>[]> {
  const query = paths.map((path) => `part=${encodeURIComponent(path)}`).join("&");
  const response = await direct(`/reads?${query}`, { signal: group.controller.signal });
  if (response.status !== 200) {
    const text = await response.text();
    const body = text === "" ? null : text;
    return paths.map(
      async () => new Response(body, { status: response.status, headers: response.headers }),
    );
  }
  const { parts } = (await response.json()) as { parts: Part[] };
  if (!Array.isArray(parts) || parts.length !== paths.length) {
    throw new Error(`GET /reads answered ${String(parts?.length)} parts for ${paths.length} paths`);
  }
  const holding = parts.find(
    (part): part is AnsweredPart =>
      part.kind === "answered" && HOLDING.has(part.status) && "retry-after" in part.headers,
  );
  return paths.map(async (path, index) => {
    const part = parts[index];
    if (part === undefined) {
      throw new Error(`GET /reads answered nothing for ${path}`);
    }
    if (part.kind === "answered") {
      return responseOf(part);
    }
    const kind: string = part.kind;
    if (kind !== "left") {
      throw new Error(`GET /reads answered ${path} with a part of no known kind`);
    }
    return holding === undefined ? alone(path, group) : responseOf(holding);
  });
}

async function answered<T>(response: Response, asked: string): Promise<T> {
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

export async function request<T>(
  path: string,
  init?: RequestInit,
  sending: Sending = WAITS,
): Promise<T> {
  const asked = `${init?.method ?? "GET"} ${path}`;
  if (sending.whileHeld === "waits") {
    refuseWhileHeld(asked);
  }
  const signal = readSignal(init?.signal);
  if (init?.method === undefined || init.method === "GET") {
    const waiting = waitForRead(signal);
    if (waiting !== undefined) await waiting;
  }
  if (sending.whileHeld === "waits") refuseWhileHeld(asked);
  if (isPart(path, init)) return grouped<T>(path, signal);
  const response = await direct(path, { ...init, signal: signal ?? null });
  return answered<T>(response, asked);
}

export const clientEventsApi = {
  record: (body: ClientEvents) =>
    request<void>(
      "/client-events",
      { method: "POST", body: JSON.stringify(body), keepalive: true },
      { whileHeld: "sends" },
    ),
};
