import { expect, test } from "vitest";
import { screens } from "./first-draw.tsx";
import { readBudgets } from "./read-budgets.ts";

test("tests/read-budgets.ts gives a budget to each screen the router serves, and to nothing else", () => {
  expect(Object.keys(readBudgets).sort()).toEqual(screens().sort());
});
