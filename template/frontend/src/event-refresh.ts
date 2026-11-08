import type { Query, QueryClient } from "@tanstack/react-query";
import { delay, type EventReadPolicy, ReadBudget, until } from "@/api/read-budget";

type Permit = { resumed: boolean };
type Attempt = { permit: Permit | undefined; canceled?: unknown };
type Pending = { dirty: boolean; done: Promise<void>; canceled?: Attempt };
type Outcome = { ok: true } | { ok: false; failure: unknown };

function outcome(work: Promise<unknown>): Promise<Outcome> {
  return work.then(
    () => ({ ok: true }),
    (failure: unknown) => ({ ok: false, failure }),
  );
}

export class EventRefresh {
  readonly budget: ReadBudget;
  private readonly pending = new Map<Query, Pending>();
  private readonly permits = new WeakMap<Query, Permit>();
  private readonly last = new WeakMap<Query, number>();
  private readonly latest = new WeakMap<Query, Attempt>();
  private readonly stopWatching: () => void;
  private readonly client: QueryClient;

  constructor(client: QueryClient, policy: EventReadPolicy) {
    this.client = client;
    this.budget = new ReadBudget(policy);
    this.stopWatching = client.getQueryCache().subscribe((event) => {
      if (
        event.type === "updated" &&
        event.action.type === "success" &&
        this.permits.has(event.query)
      ) {
        event.query.invalidate();
      }
      if (event.type === "removed") {
        this.permits.delete(event.query);
        this.pending.delete(event.query);
      }
    });
  }

  take(query: Query): Permit | undefined {
    const permit = this.permits.get(query);
    this.permits.delete(query);
    return permit;
  }

  retry(query: Query): Permit | undefined {
    return this.latest.get(query)?.permit;
  }

  begin(query: Query, permit: Permit | undefined): Attempt {
    const read = { permit };
    this.latest.set(query, read);
    return read;
  }

  watch(query: Query, read: Attempt, signal: AbortSignal): () => void {
    const abort = () => this.canceled(query, read, signal.reason);
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
    return () => signal.removeEventListener("abort", abort);
  }

  async prepare(query: Query, read: Attempt, signal: AbortSignal): Promise<void> {
    const lifetime = AbortSignal.any([signal, this.budget.lifetime.signal]);
    const last = this.last.get(query);
    const wait = last === undefined ? 0 : last + this.budget.policy.intervalMs - performance.now();
    await delay(Math.max(0, Math.ceil(wait)), lifetime);
    lifetime.throwIfAborted();
    const newer = this.take(query);
    if (newer !== undefined) read.permit = newer;
    const pending = this.pending.get(query);
    if (pending !== undefined) pending.dirty = false;
    this.last.set(query, performance.now());
  }

  change(query: Query): Promise<void> {
    this.budget.lifetime.signal.throwIfAborted();
    this.permits.set(query, { resumed: false });
    query.invalidate();
    const existing = this.pending.get(query);
    if (existing !== undefined) {
      existing.dirty = true;
      return existing.done;
    }
    const pending: Pending = { dirty: true, done: Promise.resolve() };
    this.pending.set(query, pending);
    pending.done = Promise.resolve().then(() => this.drain(query, pending));
    return pending.done;
  }

  stop(): void {
    this.stopWatching();
    this.budget.lifetime.abort();
    for (const query of this.client.getQueryCache().getAll()) {
      if (query.state.fetchStatus !== "idle" && this.latest.get(query)?.permit !== undefined) {
        void query.cancel({ revert: true });
      }
    }
    this.pending.clear();
  }

  private canceled(query: Query, read: Attempt, failure: unknown): void {
    read.canceled = failure;
    if (this.budget.lifetime.signal.aborted || this.latest.get(query) !== read) return;
    const pending = this.pending.get(query);
    if (pending !== undefined) {
      pending.canceled = read;
      pending.dirty = true;
    }
    query.invalidate();
    if (read.permit?.resumed === false && !this.permits.has(query)) {
      this.permits.set(query, { resumed: true });
    }
  }

  private settled(query: Query, pending: Pending, result: Outcome): boolean {
    if (this.pending.get(query) !== pending) return false;
    const canceled = pending.canceled;
    if (
      canceled !== undefined &&
      this.latest.get(query) === canceled &&
      this.permits.get(query)?.resumed !== false
    ) {
      throw canceled.canceled;
    }
    if (pending.dirty) query.invalidate();
    if (!(result.ok || pending.dirty)) throw result.failure;
    return pending.dirty;
  }

  private async drain(query: Query, pending: Pending): Promise<void> {
    try {
      if (query.state.fetchStatus !== "idle" && query.promise !== undefined) {
        const result = await until(outcome(query.promise), this.budget.lifetime.signal);
        if (!this.settled(query, pending, result)) return;
      }
      while (pending.dirty && this.pending.get(query) === pending) {
        if (!query.isActive()) return;
        const result = await until(
          outcome(query.fetch(undefined, { cancelRefetch: false })),
          this.budget.lifetime.signal,
        );
        if (!this.settled(query, pending, result)) return;
      }
    } finally {
      if (this.pending.get(query) === pending) this.pending.delete(query);
    }
  }
}
