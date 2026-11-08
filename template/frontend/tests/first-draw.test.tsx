import { expect, test } from "vitest";
import { type DrawnRead, readsOf, screens } from "./first-draw.tsx";
import { readBudgets, streams } from "./read-budgets.ts";
import { signedIn } from "./signed-in.ts";

function linesOf(reads: DrawnRead[]): string {
  return reads.map((read) => read.line).join("\n");
}

function readTwice(reads: DrawnRead[]): string[] {
  const urls = reads.flatMap((read) => [...new Set(read.urls)]);
  return [...new Set(urls.filter((url, index) => urls.indexOf(url) !== index))];
}

test("tests/read-budgets.ts gives a budget to each screen the router serves, and to nothing else", () => {
  expect(Object.keys(readBudgets).sort()).toEqual(screens().sort());
});

test.each(Object.entries(readBudgets))(
  "%s reads exactly its budget on the first draw, no more on a focus, and no URL in two requests",
  async (path, budget) => {
    const { landed, firstDraw, afterFocus } = await signedIn(path, () =>
      readsOf(budget.visit, streams),
    );

    expect(landed, `${budget.visit} draws another screen`).toBe(budget.lands ?? path);
    expect(firstDraw.length, `the first draw read:\n${linesOf(firstDraw)}`).toBe(budget.reads);
    expect(afterFocus.length, `a focus read:\n${linesOf(afterFocus)}`).toBeLessThanOrEqual(
      budget.reads,
    );
    expect(
      readTwice(firstDraw),
      `the first draw read in two requests:\n${linesOf(firstDraw)}`,
    ).toEqual([]);
    expect(readTwice(afterFocus), `a focus read in two requests:\n${linesOf(afterFocus)}`).toEqual(
      [],
    );
  },
);
