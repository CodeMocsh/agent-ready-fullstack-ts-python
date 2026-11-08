export type EventReadPolicy = { intervalMs: number; readsPerInterval: number };

export const EVENT_READ_POLICY: EventReadPolicy = { intervalMs: 500, readsPerInterval: 2 };

export function checkedEventReadPolicy(policy: EventReadPolicy): EventReadPolicy {
  if (
    !Number.isFinite(policy.intervalMs) ||
    policy.intervalMs <= 0 ||
    !Number.isSafeInteger(policy.readsPerInterval) ||
    policy.readsPerInterval <= 0
  ) {
    throw new Error("Event reads need a positive interval and a positive integer read limit");
  }
  return { ...policy };
}

export function until<T>(work: Promise<T>, signal: AbortSignal): Promise<T> {
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const abort = () => reject(signal.reason);
    signal.addEventListener("abort", abort, { once: true });
    work.then(resolve, reject).finally(() => signal.removeEventListener("abort", abort));
  });
}

export function delay(ms: number, signal: AbortSignal): Promise<void> {
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, ms);
    signal.addEventListener("abort", abort, { once: true });
  });
}

type WaitingRead = { signal: AbortSignal; start: () => void; abort: () => void };

export class ReadBudget {
  readonly lifetime = new AbortController();
  readonly policy: EventReadPolicy;
  private starts: number[] = [];
  private readonly waiting = new Set<WaitingRead>();
  private timer: ReturnType<typeof setTimeout> | undefined;

  constructor(policy: EventReadPolicy) {
    this.policy = checkedEventReadPolicy(policy);
  }

  wait(signal: AbortSignal): Promise<void> {
    const lifetime = AbortSignal.any([signal, this.lifetime.signal]);
    lifetime.throwIfAborted();
    return new Promise((resolve, reject) => {
      const waiting: WaitingRead = {
        signal: lifetime,
        start: resolve,
        abort: () => {
          this.remove(waiting);
          reject(lifetime.reason);
        },
      };
      this.waiting.add(waiting);
      lifetime.addEventListener("abort", waiting.abort, { once: true });
      this.schedule();
    });
  }

  private remove(waiting: WaitingRead): void {
    this.waiting.delete(waiting);
    waiting.signal.removeEventListener("abort", waiting.abort);
    if (this.waiting.size === 0) {
      clearTimeout(this.timer);
      this.timer = undefined;
    }
  }

  private schedule(): void {
    if (this.timer !== undefined) return;
    const earliest = this.starts.at(-this.policy.readsPerInterval);
    const wait =
      earliest === undefined
        ? 0
        : Math.max(0, earliest + this.policy.intervalMs - performance.now());
    this.timer = setTimeout(() => this.release(), Math.ceil(wait));
  }

  private release(): void {
    this.timer = undefined;
    const now = performance.now();
    this.starts = this.starts.filter((at) => now - at < this.policy.intervalMs);
    for (const waiting of this.waiting) {
      if (this.starts.length === this.policy.readsPerInterval) break;
      this.remove(waiting);
      this.starts.push(now);
      waiting.start();
    }
    if (this.waiting.size > 0) this.schedule();
  }
}

const budgets = new WeakMap<AbortSignal, ReadBudget>();

export function budgetEventRead(signal: AbortSignal, budget: ReadBudget): void {
  budgets.set(signal, budget);
}

export function waitForRead(signal: AbortSignal | null | undefined): Promise<void> | undefined {
  signal?.throwIfAborted();
  if (signal !== undefined && signal !== null) {
    return budgets.get(signal)?.wait(signal);
  }
}

export function readBudget(signal: AbortSignal | undefined): ReadBudget | undefined {
  return signal === undefined ? undefined : budgets.get(signal);
}

export function copyReadBudget(source: AbortSignal | undefined, target: AbortSignal): void {
  const budget = source === undefined ? undefined : budgets.get(source);
  if (budget !== undefined) budgets.set(target, budget);
}

export function readSignal(signal: AbortSignal | null | undefined): AbortSignal | undefined {
  if (signal === undefined || signal === null) return undefined;
  const budget = budgets.get(signal);
  if (budget === undefined) return signal;
  const scoped = AbortSignal.any([signal, budget.lifetime.signal]);
  budgets.set(scoped, budget);
  return scoped;
}
