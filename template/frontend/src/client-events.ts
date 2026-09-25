import { configureSync, getConsoleSink, getLogger, type LogRecord } from "@logtape/logtape";
import { ApiError, clientEventsApi } from "@/api/client";
import type { ClientEvent } from "@/api/types";
import { router } from "@/router";

export type RecordClientEvent = (kind: ClientEvent["kind"], error: unknown) => void;

const CATEGORY = ["app", "client-events"];
const UNDELIVERABLE = [...CATEGORY, "undeliverable"];
const MAX_PER_PAGE = 20;

function describe(kind: ClientEvent["kind"], error: unknown): ClientEvent {
  const refused = error instanceof ApiError ? error : null;
  return {
    kind,
    route: router.state.matches.at(-1)?.routeId ?? null,
    error: error instanceof Error ? error.name : typeof error,
    status: refused?.status ?? null,
    request_id: refused?.requestId ?? null,
  };
}

function sendToBackend(record: LogRecord): void {
  const { event } = record.properties as { event: ClientEvent };
  clientEventsApi.record({ events: [event] }).catch((failure: unknown) => {
    getLogger(UNDELIVERABLE).error("client event undeliverable", { event, failure });
  });
}

export function startClientEvents(): RecordClientEvent {
  configureSync({
    reset: true,
    sinks: { backend: sendToBackend, console: getConsoleSink() },
    loggers: [
      {
        category: CATEGORY,
        sinks: import.meta.env.DEV ? ["backend", "console"] : ["backend"],
        lowestLevel: "warning",
      },
      { category: UNDELIVERABLE, sinks: ["console"], parentSinks: "override" },
      { category: ["logtape", "meta"], sinks: ["console"], lowestLevel: "warning" },
    ],
  });
  const logger = getLogger(CATEGORY);
  const sent = new Set<string>();
  return (kind, error) => {
    const event = describe(kind, error);
    const fingerprint = `${event.kind} ${event.error} ${event.route} ${event.status}`;
    if (sent.has(fingerprint) || sent.size >= MAX_PER_PAGE) {
      return;
    }
    sent.add(fingerprint);
    logger.warn("client event", { event });
  };
}
