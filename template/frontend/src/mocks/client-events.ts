import type { ClientEvent } from "@/api/types";

const FIELDS = ["kind", "route", "error", "status", "request_id"];
const KINDS: ReadonlySet<unknown> = new Set<ClientEvent["kind"]>([
  "uncaught",
  "caught",
  "query",
  "mutation",
]);
const ROUTE = /^[A-Za-z0-9_$./-]+$/;
const ERROR_NAME = /^[A-Za-z][A-Za-z0-9_]*$/;
const REQUEST_ID = /^[0-9a-f]{32}$/;
const MAX_EVENTS = 20;

function matches(value: unknown, pattern: RegExp, maxLength: number): boolean {
  return typeof value === "string" && value.length <= maxLength && pattern.test(value);
}

function isStatus(value: unknown): boolean {
  return Number.isInteger(value) && (value as number) >= 100 && (value as number) <= 599;
}

function isClientEvent(value: unknown): boolean {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const event = value as Record<string, unknown>;
  const keys = Object.keys(event);
  return (
    keys.length === FIELDS.length &&
    FIELDS.every((field) => keys.includes(field)) &&
    KINDS.has(event.kind) &&
    (event.route === null || matches(event.route, ROUTE, 200)) &&
    matches(event.error, ERROR_NAME, 100) &&
    (event.status === null || isStatus(event.status)) &&
    (event.request_id === null || matches(event.request_id, REQUEST_ID, 32))
  );
}

export function acceptsClientEvents(body: unknown): boolean {
  if (typeof body !== "object" || body === null) {
    return false;
  }
  const { events, ...rest } = body as { events?: unknown };
  return (
    Object.keys(rest).length === 0 &&
    Array.isArray(events) &&
    events.length >= 1 &&
    events.length <= MAX_EVENTS &&
    events.every(isClientEvent)
  );
}
