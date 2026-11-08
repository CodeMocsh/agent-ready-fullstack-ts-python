export type EventReadPolicy = { intervalMs: number; readsPerInterval: number };

export const EVENT_READ_POLICY: EventReadPolicy = { intervalMs: 500, readsPerInterval: 2 };

export function checkedEventReadPolicy(policy: EventReadPolicy): EventReadPolicy {
  if (!validPolicy(policy)) {
    throw new Error("Event reads need a positive interval and a positive integer read limit");
  }
  return { ...policy };
}

function validPolicy(policy: EventReadPolicy): boolean {
  return (
    Number.isFinite(policy.intervalMs) &&
    policy.intervalMs > 0 &&
    Number.isSafeInteger(policy.readsPerInterval) &&
    policy.readsPerInterval > 0
  );
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

type WaitingRead = {
  signal: AbortSignal;
  start: () => void;
  abort: () => void;
  outsideCause: () => void;
};

export class ReadBudget {
  readonly lifetime = new AbortController();
  readonly policy: EventReadPolicy;
  private starts: number[] = [];
  private readonly waiting = new Set<WaitingRead>();
  private timer: ReturnType<typeof setTimeout> | undefined;

  constructor(policy: EventReadPolicy) {
    this.policy = checkedEventReadPolicy(policy);
  }

  wait(signal: AbortSignal, outsideCause: () => void = () => undefined): Promise<void> {
    const lifetime = AbortSignal.any([signal, this.lifetime.signal]);
    lifetime.throwIfAborted();
    return new Promise((resolve, reject) => {
      const waiting: WaitingRead = {
        signal: lifetime,
        outsideCause,
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

  outsideCause(): void {
    for (const waiting of this.waiting) {
      this.remove(waiting);
      waiting.outsideCause();
      waiting.start();
    }
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

type BudgetedRead = { budget: ReadBudget; admitted: boolean; outsideCause: () => void };

const budgets = new WeakMap<AbortSignal, BudgetedRead>();

export function budgetEventRead(
  signal: AbortSignal,
  budget: ReadBudget,
  outsideCause: () => void = () => undefined,
): void {
  budgets.set(signal, { budget, admitted: false, outsideCause });
}

export function waitForRead(signal: AbortSignal | null | undefined): Promise<void> | undefined {
  signal?.throwIfAborted();
  if (signal == null) return undefined;
  const read = budgets.get(signal);
  if (read !== undefined) return admittedRead(read, signal);
}

async function admittedRead(read: BudgetedRead, signal: AbortSignal): Promise<void> {
  await read.budget.wait(signal, read.outsideCause);
  read.admitted = true;
}

export function eventReadAdmitted(signal: AbortSignal): boolean {
  return budgets.get(signal)?.admitted === true;
}

function budgetedRead(signal: AbortSignal | undefined): BudgetedRead | undefined {
  return signal === undefined ? undefined : budgets.get(signal);
}

export function readBudget(signal: AbortSignal | undefined): ReadBudget | undefined {
  return budgetedRead(signal)?.budget;
}

export function copyReadBudget(source: AbortSignal | undefined, target: AbortSignal): void {
  const read = budgetedRead(source);
  if (read !== undefined) budgets.set(target, read);
}

export function readSignal(signal: AbortSignal | null | undefined): AbortSignal | undefined {
  if (signal == null) return undefined;
  const read = budgets.get(signal);
  if (read === undefined) return signal;
  const scoped = AbortSignal.any([signal, read.budget.lifetime.signal]);
  budgets.set(scoped, read);
  return scoped;
}
