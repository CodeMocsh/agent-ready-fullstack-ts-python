import type { Query, QueryCacheNotifyEvent, QueryClient } from "@tanstack/react-query";
import {
  delay,
  type EventReadPolicy,
  eventReadAdmitted,
  ReadBudget,
  until,
} from "@/api/read-budget";

type Permit = { resumed: boolean };
type Attempt = { permit: Permit | undefined; canceled?: unknown; continuation?: Permit };
type Pending = { dirty: boolean; done: Promise<void>; canceled?: Attempt };
type Outcome = { ok: true } | { ok: false; failure: unknown };

function outcome(work: Promise<unknown>): Promise<Outcome> {
  return work.then(
    () => ({ ok: true }),
    (failure: unknown) => ({ ok: false, failure }),
  );
}

function assertSuccessful(result: Outcome): void {
  if (!result.ok) throw result.failure;
}

export class EventRefreshStopped extends Error {
  override readonly name = "EventRefreshStopped";
}

export class EventReadCanceled extends Error {
  override readonly name = "EventReadCanceled";
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
    this.stopWatching = client.getQueryCache().subscribe((event) => this.observed(event));
  }

  private observed(event: QueryCacheNotifyEvent): void {
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
    const abort = () => this.canceled(query, read, signal);
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
    this.budget.lifetime.abort(
      new EventRefreshStopped("The query client stopped following events"),
    );
    for (const query of this.client.getQueryCache().getAll()) {
      this.cancelEventRead(query);
    }
    this.pending.clear();
  }

  private cancelEventRead(query: Query): void {
    if (query.state.fetchStatus === "idle" || this.latest.get(query)?.permit === undefined) return;
    void query.cancel({ revert: true });
  }

  private canceled(query: Query, read: Attempt, signal: AbortSignal): void {
    read.canceled = new EventReadCanceled("The query canceled its event read", {
      cause: signal.reason,
    });
    if (this.budget.lifetime.signal.aborted || this.latest.get(query) !== read) return;
    const pending = this.pending.get(query);
    if (pending !== undefined) {
      pending.canceled = read;
      pending.dirty = true;
    }
    query.invalidate();
    this.continueCanceled(query, read, signal);
  }

  private continueCanceled(query: Query, read: Attempt, signal: AbortSignal): void {
    const permit = read.permit;
    if (permit === undefined || this.permits.has(query)) return;
    const admitted = eventReadAdmitted(signal);
    if (admitted && permit.resumed) return;
    read.continuation = { resumed: admitted || permit.resumed };
    this.permits.set(query, read.continuation);
  }

  private settled(query: Query, pending: Pending, result: Outcome): boolean {
    if (this.pending.get(query) !== pending) return false;
    this.assertNotCanceled(query, pending);
    if (pending.dirty) {
      query.invalidate();
      return true;
    }
    assertSuccessful(result);
    return false;
  }

  private assertNotCanceled(query: Query, pending: Pending): void {
    const canceled = pending.canceled;
    if (
      canceled !== undefined &&
      this.latest.get(query) === canceled &&
      this.permits.get(query) === canceled.continuation
    ) {
      throw canceled.canceled;
    }
  }

  private async drain(query: Query, pending: Pending): Promise<void> {
    try {
      if (!(await this.finishActive(query, pending))) return;
      await this.readChanges(query, pending);
    } finally {
      if (this.pending.get(query) === pending) this.pending.delete(query);
    }
  }

  private async finishActive(query: Query, pending: Pending): Promise<boolean> {
    if (query.state.fetchStatus === "idle" || query.promise === undefined) return true;
    const result = await until(outcome(query.promise), this.budget.lifetime.signal);
    return this.settled(query, pending, result);
  }

  private needsRead(query: Query, pending: Pending): boolean {
    return pending.dirty && this.pending.get(query) === pending && query.isActive();
  }

  private async readChanges(query: Query, pending: Pending): Promise<void> {
    while (this.needsRead(query, pending)) {
      const result = await until(
        outcome(query.fetch(undefined, { cancelRefetch: false })),
        this.budget.lifetime.signal,
      );
      if (!this.settled(query, pending, result)) return;
    }
  }
}
