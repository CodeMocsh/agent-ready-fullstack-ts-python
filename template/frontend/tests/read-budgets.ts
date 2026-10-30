import type { ReadBudget } from "./first-draw.tsx";

export type { ReadBudget } from "./first-draw.tsx";

export const readBudgets: Record<string, ReadBudget> = {
  "/": { visit: "/", reads: 1 },
};

export const streams: readonly RegExp[] = [];
