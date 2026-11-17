import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { gate } from "../harness";

const EXPORTED_ARROWS = [
  "export const typed = (id: string): string => id;",
  "export const asynchronous = async (id: string) => id;",
  "export const generic = <T,>(value: T): T => value;",
  "export const nested = <T extends Record<string, unknown>>(value: T): T => value;",
  "export const annotated: (id: string) => string = (id) => id;",
  "export const bare = id => id;",
  "export const expression = function () { return 1; };",
];

const conformance = gate("conformance.mjs", "conformance");

function scan(name: string, lines: string[]) {
  const { status, lines: reported } = conformance.run(name, lines);
  const rules = reported.map((line) => line.split(/\s{2,}/)[1]);
  return { status, rules: [...new Set(rules)].sort() };
}

describe("the conformance gate", () => {
  beforeEach(conformance.open);

  afterEach(conformance.close);

  it("catches every exported arrow form, including typed, async and generic", () => {
    for (const [index, line] of EXPORTED_ARROWS.entries()) {
      const { status, rules } = scan(`arrow-${index}.ts`, [line]);
      expect({ line, rules }).toEqual({ line, rules: ["exported-function-expression"] });
      expect(status).toBe(1);
    }
  });
});
