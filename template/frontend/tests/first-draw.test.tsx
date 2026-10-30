import { expect, test } from "vitest";
import { readsOf, screens } from "./first-draw.tsx";
import { readBudgets, streams } from "./read-budgets.ts";

test("tests/read-budgets.ts gives a budget to each screen the router serves, and to nothing else", () => {
  expect(Object.keys(readBudgets).sort()).toEqual(screens().sort());
});

test.each(Object.entries(readBudgets))(
  "%s reads no more than its budget on the first draw, and again on a focus",
  async (path, budget) => {
    const { landed, firstDraw, afterFocus } = await readsOf(budget.visit, streams);

    expect(landed, `${budget.visit} draws another screen`).toBe(path);
    expect(firstDraw.length, `the first draw read:\n${firstDraw.join("\n")}`).toBeLessThanOrEqual(
      budget.reads,
    );
    expect(afterFocus.length, `a focus read:\n${afterFocus.join("\n")}`).toBeLessThanOrEqual(
      budget.reads,
    );
  },
);
