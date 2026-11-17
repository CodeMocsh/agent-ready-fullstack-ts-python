import { existsSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { gate, NO_PROJECT_RULES } from "./harness";

const DEBT = ["--debt", "debt.json"];

const TWO_COLOURS = [
  "export function Swatch() {",
  "  return <p className='border-[#fff] bg-[#000]'>two</p>;",
  "}",
];

const ONE_COLOUR = [
  "export function Swatch() {",
  "  return <p className='border-[#fff]'>one</p>;",
  "}",
];

const CLEAN = [
  "export function Swatch() {",
  "  return <p className='border-border'>none</p>;",
  "}",
];

const OTHER = ["export function Other() {", "  return null;", "}"];

const FAINT_RULE =
  "export const RULES = [{ name: 'hairline-as-text', files: /\\.tsx$/, " +
  "pattern: /text-faint/g, hint: 'faint is a hairline colour' }];";

const FAINT = ["export function Swatch() {", "  return <p className='text-faint'>faint</p>;", "}"];

const conformance = gate("conformance.mjs", "debt");

function debtFile() {
  return conformance.at("debt.json");
}

function debt() {
  return JSON.parse(readFileSync(debtFile(), "utf8"));
}

function run(source: string[], args: string[] = [], rulesModule = NO_PROJECT_RULES) {
  return conformance.run("swatch.tsx", source, rulesModule, [...DEBT, ...args]);
}

function rulesOf(lines: string[]) {
  return lines.map((line) => line.split(/\s{2,}/)[1]);
}

describe("the conformance debt on a file's count", () => {
  beforeEach(conformance.open);

  afterEach(conformance.close);

  it("records the findings of each rule, and lets the file keep them", () => {
    const recorded = run(TWO_COLOURS, ["--record"]);
    expect(debt().files).toEqual({ [recorded.path]: { "raw-colour": 2 } });
    expect(debt().rules).toContain("raw-colour");
    const checked = run(TWO_COLOURS);
    expect(checked.stdout).toContain("2 findings owed in the debt");
    expect(checked.status).toBe(0);
  });

  it("refuses a finding past the count, and says a raise is a hand edit", () => {
    run(ONE_COLOUR, ["--record"]);
    const { status, stderr, lines } = run(TWO_COLOURS);
    expect(rulesOf(lines)).toEqual(["raw-colour", "raw-colour"]);
    expect(stderr).toContain("Do not raise a count");
    expect(status).toBe(1);
  });

  it("never raises a count, when it records or when it tightens", () => {
    const { path } = run(ONE_COLOUR, ["--record"]);
    const { status } = run(TWO_COLOURS, ["--record", "--tighten"]);
    expect(debt().files).toEqual({ [path]: { "raw-colour": 1 } });
    expect(status).toBe(1);
  });

  it("refuses a count the tree no longer needs, and tighten lowers it", () => {
    const { path } = run(TWO_COLOURS, ["--record"]);
    const loose = run(ONE_COLOUR);
    expect(loose.stderr).toContain(`${path}  raw-colour  2 -> 1`);
    expect(loose.status).toBe(1);
    const tightened = run(ONE_COLOUR, ["--tighten"]);
    expect(tightened.stdout).toContain("conformance debt lowered");
    expect(debt().files).toEqual({ [path]: { "raw-colour": 1 } });
    expect(tightened.status).toBe(0);
  });

  it("leaves the file as written when it only checks", () => {
    run(TWO_COLOURS, ["--record"]);
    const written = JSON.stringify(debt());
    writeFileSync(debtFile(), written);
    run(ONE_COLOUR);
    expect(readFileSync(debtFile(), "utf8")).toBe(written);
  });
});

describe("the conformance debt on what it no longer needs", () => {
  beforeEach(conformance.open);

  afterEach(conformance.close);

  it("refuses an entry for a file that is gone, and says where its counts go", () => {
    const { path } = run(TWO_COLOURS, ["--record"]);
    rmSync(path);
    const stale = conformance.run("other.tsx", OTHER, NO_PROJECT_RULES, DEBT);
    expect(stale.stderr).toContain(`${path}  (gone; if you renamed it, put its counts under`);
    expect(stale.status).toBe(1);
    const tightened = conformance.run("other.tsx", OTHER, NO_PROJECT_RULES, [...DEBT, "--tighten"]);
    expect(debt().files).toEqual({});
    expect(tightened.status).toBe(0);
  });

  it("refuses an entry for a file it no longer reads", () => {
    const { path } = run(TWO_COLOURS, ["--record"]);
    const excluded = conformance.run("other.tsx", OTHER, NO_PROJECT_RULES, [
      ...DEBT,
      "--exclude",
      path,
    ]);
    expect(excluded.stderr).toContain(`${path}  (not a file this gate reads)`);
    expect(excluded.status).toBe(1);
  });

  it("refuses a rule that no longer exists, and tighten deletes its name", () => {
    run(CLEAN, ["--record"]);
    writeFileSync(debtFile(), JSON.stringify({ ...debt(), rules: [...debt().rules, "gone"] }));
    const stale = run(CLEAN);
    expect(stale.stderr).toContain("rule gone  (no such rule)");
    expect(stale.status).toBe(1);
    run(CLEAN, ["--tighten"]);
    expect(debt().rules).not.toContain("gone");
  });
});

describe("the conformance debt on a rule it does not name yet", () => {
  beforeEach(conformance.open);

  afterEach(conformance.close);

  it("asks for a record, and the record counts that rule only", () => {
    run(CLEAN, ["--record"]);
    const source = [...FAINT, "export const ink = 'border-[#fff]';"];
    const asked = run(source, [], FAINT_RULE);
    expect(asked.stderr).toContain("does not name yet");
    expect(asked.stderr).toContain("pnpm conformance:record");
    expect(asked.status).toBe(1);
    const recorded = run(source, ["--record"], FAINT_RULE);
    expect(debt().files).toEqual({ [recorded.path]: { "hairline-as-text": 1 } });
    expect(rulesOf(recorded.lines)).toEqual(["raw-colour"]);
    expect(recorded.status).toBe(1);
  });

  it("names a new rule that finds nothing when it tightens, so a later finding is over", () => {
    run(CLEAN, ["--record"]);
    const tightened = run(CLEAN, ["--tighten"], FAINT_RULE);
    expect(tightened.stdout).toContain("names 1 new rule");
    expect(debt().rules).toContain("hairline-as-text");
    const { status, stderr } = run(FAINT, [], FAINT_RULE);
    expect(stderr).not.toContain("does not name yet");
    expect(stderr).toContain("Do not raise a count");
    expect(status).toBe(1);
  });

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
