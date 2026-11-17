import { existsSync, writeFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { CLEAN, debtGate, rulesOf, TWO_COLOURS } from "./debt-gate";
import { NO_PROJECT_RULES } from "./harness";

const { conformance, debtFile, run } = debtGate();

describe("the conformance debt on a rule it does not name yet", () => {
  beforeEach(conformance.open);

  afterEach(conformance.close);

  it("counts every rule as zero, and names no debt, when there is no file", () => {
    const { status, stderr, lines } = run(TWO_COLOURS, ["--tighten"]);
    expect(rulesOf(lines)).toEqual(["raw-colour", "raw-colour"]);
    expect(stderr).not.toContain("debt.json");
    expect(existsSync(debtFile())).toBe(false);
    expect(status).toBe(1);
  });
});

describe("the conformance debt on input it cannot use", () => {
  beforeEach(conformance.open);

  afterEach(conformance.close);

  it.each([
    ["it is not JSON", "{ rules: [] }", "is not JSON"],
    [
      "it has a key of its own",
      '{"rules": [], "files": {}, "notes": ""}',
      "exactly rules and files",
    ],
    ["a rule is named twice", '{"rules": ["a", "a"], "files": {}}', "names a rule twice"],
    ["a count is zero", '{"rules": ["a"], "files": {"x.tsx": {"a": 0}}}', "counts 0 a for x.tsx"],
    [
      "a count is for no named rule",
      '{"rules": [], "files": {"x.tsx": {"a": 1}}}',
      "rules does not name it",
    ],
  ])("refuses to run when %s", (_case, text, problem) => {
    writeFileSync(debtFile(), text);
    const { status, stderr } = run(CLEAN);
    expect(stderr).toContain(problem);
    expect(status).toBe(2);
  });

  it.each([
    ["--record without --debt", ["--record"], "need --debt"],
    ["--debt without a value", ["--debt"], "needs a value"],
    ["--debt followed by a flag", ["--debt", "--tighten"], "needs a value"],
  ])("refuses %s", (_case, args, problem) => {
    const { status, stderr } = conformance.run("swatch.tsx", CLEAN, NO_PROJECT_RULES, args);
    expect(stderr).toContain(problem);
    expect(status).toBe(2);
  });
});
