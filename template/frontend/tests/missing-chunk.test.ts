import { describe, expect, it, vi } from "vitest";
import { reloadOnceOnMissingChunk, type Session } from "@/missing-chunk";

const A_BUILD = "https://example.test/assets/index-abc123.js";
const THE_NEXT_BUILD = "https://example.test/assets/index-def456.js";

function aSession(): () => Session {
  const held = new Map<string, string>();
  const session: Session = {
    getItem: (key) => held.get(key) ?? null,
    setItem: (key, value) => {
      held.set(key, value);
    },
  };
  return () => session;
}

function blocked(): Session {
  throw new DOMException("The operation is insecure.", "SecurityError");
}

function aTab(session: () => Session, build: string): { miss: () => Event; reload: () => void } {
  const events = new EventTarget();
  const reload = vi.fn();
  reloadOnceOnMissingChunk(
    {
      addEventListener: events.addEventListener.bind(events) as Window["addEventListener"],
      location: { reload },
    },
    session,
    build,
  );
  return {
    miss: () => {
      const event = new Event("vite:preloadError", { cancelable: true });
      events.dispatchEvent(event);
      return event;
    },
    reload,
  };
}

describe("a missing chunk", () => {
  it("reloads the tab and lets the error through", () => {
    const tab = aTab(aSession(), A_BUILD);

    const event = tab.miss();

    expect(tab.reload).toHaveBeenCalledOnce();
    expect(event.defaultPrevented).toBe(false);
  });

  it("lets the error through without a second reload when the reloaded tab runs the same build", () => {
    const session = aSession();
    aTab(session, A_BUILD).miss();
    const reloaded = aTab(session, A_BUILD);

    const event = reloaded.miss();

    expect(event.defaultPrevented).toBe(false);
    expect(reloaded.reload).not.toHaveBeenCalled();
  });

  it("reloads again when a later build misses a chunk", () => {
    const session = aSession();
    aTab(session, A_BUILD).miss();
    const later = aTab(session, THE_NEXT_BUILD);

    later.miss();

    expect(later.reload).toHaveBeenCalledOnce();
  });

  it("remembers a reload in the session it is given, not in the page", () => {
    aTab(aSession(), A_BUILD).miss();
    const another = aTab(aSession(), A_BUILD);

    another.miss();

    expect(another.reload).toHaveBeenCalledOnce();
  });

  it("reports blocked session storage and lets the error through without a reload", () => {
    const tab = aTab(blocked, A_BUILD);
    const reported: unknown[] = [];
    const report = (uncaught: ErrorEvent): void => {
      uncaught.preventDefault();
      reported.push(uncaught.error);
    };
    window.addEventListener("error", report);

    const event = tab.miss();

    window.removeEventListener("error", report);
    expect(reported).toEqual([expect.objectContaining({ name: "SecurityError" })]);
    expect(event.defaultPrevented).toBe(false);
    expect(tab.reload).not.toHaveBeenCalled();
  });
});
