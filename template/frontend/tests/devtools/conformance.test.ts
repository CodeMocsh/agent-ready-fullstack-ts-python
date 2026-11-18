import { rmSync, writeFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { gate, NO_PROJECT_RULES } from "./harness";

const OFFENDING_MARKUP = [
  "import { useEffect, useState } from 'react';",
  "export const load = (id: string): string => id.trim();",
  "export function Wrong() {",
  "  const [items, setItems] = useState<string[]>([]);",
  "  useEffect(() => {",
  "    fetch('/api/items').then(async (response) => setItems(await response.json()));",
  "  }, []);",
  "  useEffect(() => void client.invalidateQueries({ queryKey: ['items'] }), [items]);",
  "  useLayoutEffect(() => save.mutate(items), [items]);",
  "  useEffect(() => void form.current?.requestSubmit(), [items]);",
  "  useEffect(() => void invalidateForServerEvent(client, [['items']]), [items]);",
  "  return (",
  "    <div className='bg-blue-500 border-[#1a1a1a] text-[13px] p-[7px] bg-primary/80'>",
  "      <span className='bg-[rebeccapurple] stroke-2' strokeWidth={1.5} />",
  "      <p style={{ fontSize: 13, color: 'rgb(1,2,3)' }}>{items.length}</p>",
  "      <button className='rounded-lg text-sm gap-7'>add</button>",
  "    </div>",
  "  );",
  "}",
];

const OFFENDING_RULES = [
  "arbitrary-spacing",
  "arbitrary-type",
  "effect-data",
  "effect-input",
  "effect-mutation",
  "element-outside-a-primitive",
  "exported-function-expression",
  "filename-export",
  "inline-type-declaration",
  "magic-presentation-prop",
  "named-colour",
  "palette-utility",
  "query-invalidation",
  "raw-colour",
  "raw-stroke",
  "server-event-outside-a-stream",
  "spacing-off-the-scale",
  "surface-outside-a-primitive",
  "text-outside-a-role",
  "token-alpha",
];

const OFFENDING_STYLESHEET = [
  ".thing {",
  "  color: #fff;",
  "  font-family: Inter, sans-serif;",
  "  stroke-width: 2;",
  "}",
];

const STYLESHEET_RULES = ["raw-colour", "raw-stroke", "raw-type-declaration"];

const AFTER_A_REGEX = [
  "import { useEffect, useState } from 'react';",
  "const SLASH_OR_STAR = /[/*]/;",
  "export function Probe() {",
  "  const [items, setItems] = useState<string[]>([]);",
  "  useEffect(() => {",
  "    fetch('/api/items').then(async (response) => setItems(await response.json()));",
  "  }, []);",
  "  return <p style={{ fontSize: 13 }}>{SLASH_OR_STAR.source}{items.length}</p>;",
  "}",
];

const AFTER_A_REGEX_RULES = ["effect-data", "inline-type-declaration"];

const CLEAN = [
  "import { useEffect } from 'react';",
  "const ICON_STROKE = 1.5;",
  "type Props = { onResize: () => void; fontSize: 'compact' | 'comfortable' };",
  "export function Good({ onResize, fontSize }: Props) {",
  "  useEffect(() => {",
  "    window.addEventListener('resize', onResize);",
  "    return () => window.removeEventListener('resize', onResize);",
  "  }, [onResize]);",
  "  return (",
  "    <div className='w-[240px] max-w-[65ch] min-h-[200px] p-4'>",
  "      <p className='text-left text-ellipsis border-t-border'>{fontSize}</p>",
  "      <svg className='stroke-muted-foreground stroke-[length:var(--icon-stroke)]'>",
  "        <path className='stroke-(length:--icon-stroke)' strokeWidth={ICON_STROKE} />",
  "      </svg>",
  "    </div>",
  "  );",
  "}",
];

const MULTILINE_ARROW = ["export const wrapped = (", "  id: string,", "): string => id;"];

const CONSTRAINED_ARROW = [
  "export const constrained = <T extends (input: string) => string>(value: T) => value;",
];

const NOT_ARROWS = [
  "export const ICON_STROKE = 1.5;",
  "export const NAMES = ['a', 'b'];",
  "export const LABELS = NAMES.map((name) => name.toUpperCase());",
  "export const DERIVED: readonly string[] = NAMES.map((name) => name.trim());",
  "export const HANDLERS: Record<string, () => void> = { a() {} };",
  "export function proper(id: string): string {",
  "  return id;",
  "}",
];

const conformance = gate("conformance.mjs", "conformance");

function scan(name: string, lines: string[], rulesModule = NO_PROJECT_RULES, args: string[] = []) {
  const {
    status,
    stdout,
    stderr,
    lines: reported,
  } = conformance.run(name, lines, rulesModule, args);
  const rules = reported.map((line) => line.split(/\s{2,}/)[1]);
  return { status, stdout, stderr, rules: [...new Set(rules)].sort() };
}

describe("the conformance gate", () => {
  beforeEach(conformance.open);

  afterEach(conformance.close);

  it("fires every markup rule on markup that breaks all of them", () => {
    writeFileSync(conformance.at("src/index.css"), "@theme { --spacing: initial; }\n");
    const { status, rules } = scan("bad.tsx", OFFENDING_MARKUP);
    expect(rules).toEqual(OFFENDING_RULES);
    expect(status).toBe(1);
  });

  it("fires every stylesheet rule on a stylesheet that breaks all of them", () => {
    const { status, rules } = scan("bad.css", OFFENDING_STYLESHEET);
    expect(rules).toEqual(STYLESHEET_RULES);
    expect(status).toBe(1);
  });

  it("still reads the code after a regex holding a comment marker", () => {
    const { status, rules } = scan("probe.tsx", AFTER_A_REGEX);
    expect(rules).toEqual(AFTER_A_REGEX_RULES);
    expect(status).toBe(1);
  });

  it("reports nothing in markup that only looks like it breaks them", () => {
    const { status, rules } = scan("good.tsx", CLEAN);
    expect(rules).toEqual([]);
    expect(status).toBe(0);
  });

  it("lets a size with a line height past the alpha rule", () => {
    const sized = [
      "export function Sized() {",
      "  return <p className='text-sm/6'>sized</p>;",
      "}",
    ];
    const { status, rules } = scan("sized.tsx", sized, NO_PROJECT_RULES, [
      "--primitive",
      conformance.at("tree/sized.tsx"),
    ]);
    expect(rules).toEqual([]);
    expect(status).toBe(0);
  });

  it("catches an exported arrow whose parameters span lines", () => {
    const { status, rules } = scan("wrapped.ts", MULTILINE_ARROW);
    expect(rules).toEqual(["exported-function-expression"]);
    expect(status).toBe(1);
  });

  it("catches an exported arrow whose type parameter holds a function type", () => {
    const { status, rules } = scan("constrained.ts", CONSTRAINED_ARROW);
    expect(rules).toEqual(["exported-function-expression"]);
    expect(status).toBe(1);
  });

  it("leaves an exported value that is not a function alone", () => {
    const { status, rules } = scan("values.ts", NOT_ARROWS);
    expect(rules).toEqual([]);
    expect(status).toBe(0);
  });

  it("counts exactly the checks this table demonstrates", () => {
    const demonstrated = new Set([...OFFENDING_RULES, ...STYLESHEET_RULES]);
    const { stdout } = scan("good.tsx", CLEAN);
    expect(stdout).toContain(`${demonstrated.size} checks`);
  });
});

describe("the conformance gate where no package.json configures it", () => {
  beforeEach(conformance.open);

  afterEach(conformance.close);

  it("fails, and checks nothing", () => {
    rmSync(conformance.at("package.json"));
    const { status, stderr } = scan("good.tsx", CLEAN);
    expect(stderr).toContain("no package.json in");
    expect(status).toBe(2);
  });
});

describe("the conformance gate on what starts a read or a write", () => {
  beforeEach(conformance.open);

  afterEach(conformance.close);

  it.each([
    ["a click", "button.current?.click()"],
    ["a submit", "form.current?.requestSubmit()"],
    ["an event", "window.dispatchEvent(new Event('keydown'))"],
  ])("refuses an effect that fakes %s", (_input, faked) => {
    const { status, rules } = scan("faked.tsx", [
      "import { useEffect } from 'react';",
      "export function Faked() {",
      `  useEffect(() => void ${faked}, [items]);`,
      "  return null;",
      "}",
    ]);
    expect(rules).toEqual(["effect-input"]);
    expect(status).toBe(1);
  });

  it("lets an effect register a listener whose handler starts a mutation", () => {
    const { status, rules } = scan("shortcut.tsx", [
      "import { useEffect } from 'react';",
      "export function Shortcut() {",
      "  useEffect(() => {",
      "    const onKey = (event: KeyboardEvent) => event.key === 's' && save.mutate(draft);",
      "    window.addEventListener('keydown', onKey);",
      "    return () => window.removeEventListener('keydown', onKey);",
      "  }, []);",
      "  return null;",
      "}",
    ]);
    expect(rules).toEqual([]);
    expect(status).toBe(0);
  });

  it("lets a stream under src/streams/ report a server event", () => {
    const { status, rules } = scan("src/streams/tasks.ts", [
      "import { invalidateForServerEvent } from '@/query-client';",
      "export function heard(client: QueryClient): Promise<void> {",
      "  return invalidateForServerEvent(client, [['tasks']]);",
      "}",
    ]);
    expect(rules).toEqual([]);
    expect(status).toBe(0);
  });
});

const FAINT_TEXT = [
  "export function Faint() {",
  "  return <p className='text-faint border-[#fff]'>hairline</p>;",
  "}",
];

const HAIRLINE = {
  name: "'hairline-as-text'",
  files: "/\\.tsx$/",
  pattern: "/(?<![\\w-])text-faint(?![\\w-])/g",
  hint: "'faint is a hairline colour, not a text colour'",
};

const LAZY_CHECK = {
  name: "'lazy-import'",
  hint: "'import the screen statically'",
  find: "(_file, code) => [...code.matchAll(/\\blazy\\(/g)].map((match) => ({ index: match.index, text: 'lazy' }))",
};

type RuleSource = Record<string, string | undefined>;

function ruleSource(rule: RuleSource) {
  const fields = Object.entries(rule)
    .filter(([, value]) => value !== undefined)
    .map(([key, value]) => `${key}: ${value}`);
  return `{ ${fields.join(", ")} }`;
}

function rulesOf(...rules: RuleSource[]) {
  return `export const RULES = [${rules.map(ruleSource).join(", ")}];`;
}

describe("the conformance gate on the project's own rules", () => {
  beforeEach(conformance.open);

  afterEach(conformance.close);

  it("runs a project rule beside the template's own", () => {
    const { status, stderr, rules } = scan("faint.tsx", FAINT_TEXT, rulesOf(HAIRLINE));
    expect(rules).toEqual(["hairline-as-text", "raw-colour"]);
    expect(stderr).toContain("hairline-as-text: faint is a hairline colour, not a text colour");
    expect(status).toBe(1);
  });

  it("runs a project code check over the code without its strings", () => {
    const { status, stderr, rules } = scan(
      "lazy.tsx",
      ["const NOTE = 'lazy(';", "export const Screen = lazy(() => import('./screen'));"],
      rulesOf(LAZY_CHECK),
    );
    expect(rules).toEqual(["lazy-import"]);
    expect(stderr).toContain("lazy.tsx:2  lazy-import  lazy");
    expect(stderr).not.toContain("lazy.tsx:1");
    expect(status).toBe(1);
  });

  it("counts each project rule among its checks", () => {
    const demonstrated = new Set([...OFFENDING_RULES, ...STYLESHEET_RULES]);
    const { stdout } = scan("good.tsx", CLEAN, rulesOf(HAIRLINE, LAZY_CHECK));
    expect(stdout).toContain(`${demonstrated.size + 2} checks`);
  });
});

describe("the conformance gate on a project rule it cannot run", () => {
  beforeEach(conformance.open);

  afterEach(conformance.close);

  it.each([
    ["RULES is not exported", "export const RUELS = [];", "must export RULES as an array"],
    ["RULES is not an array", "export const RULES = {};", "must export RULES as an array"],
    ["a rule is not an object", "export const RULES = ['hairline-as-text'];", "is not an object"],
    ["a rule has a key of its own", rulesOf({ ...HAIRLINE, allow: "['x']" }), "has the keys"],
    ["a rule mixes both shapes", rulesOf({ ...HAIRLINE, find: LAZY_CHECK.find }), "has the keys"],
    ["a name is missing", rulesOf({ ...HAIRLINE, name: undefined }), "has the keys"],
    ["a name has a space", rulesOf({ ...HAIRLINE, name: "'hairline as text'" }), "kebab-case"],
    ["a hint is blank", rulesOf({ ...HAIRLINE, hint: "'  '" }), "needs a hint"],
    ["files has the g flag", rulesOf({ ...HAIRLINE, files: "/\\.tsx$/g" }), "needs files"],
    ["files has the y flag", rulesOf({ ...HAIRLINE, files: "/\\.tsx$/y" }), "needs files"],
    ["pattern is a string", rulesOf({ ...HAIRLINE, pattern: "'text-faint'" }), "needs pattern"],
    ["pattern has no g flag", rulesOf({ ...HAIRLINE, pattern: "/text-faint/" }), "needs pattern"],
    [
      "pattern has the y flag",
      rulesOf({ ...HAIRLINE, pattern: "/text-faint/gy" }),
      "needs pattern",
    ],
    ["pattern matches empty text", rulesOf({ ...HAIRLINE, pattern: "/q*/g" }), "empty text"],
    ["pattern matches between words", rulesOf({ ...HAIRLINE, pattern: "/\\b/g" }), "empty text"],
    [
      "find returns no index",
      rulesOf({ ...LAZY_CHECK, find: "() => [{ text: 'x' }]" }),
      "returned",
    ],
    ["find returns no array", rulesOf({ ...LAZY_CHECK, find: "() => null" }), "returned"],
    ["find is not a function", rulesOf({ ...LAZY_CHECK, find: "'lazy('" }), "needs find"],
  ])("refuses to run any rule when %s", (_case, rulesModule, problem) => {
    const { status, stderr, rules } = scan("faint.tsx", FAINT_TEXT, rulesModule);
    expect(stderr).toContain(problem);
    expect(rules).toEqual([]);
    expect(status).toBe(2);
  });

  it.each([
    ["a pattern rule", "raw-colour", []],
    ["a code check", "effect-data", []],
    ["another project rule", "hairline-as-text", [HAIRLINE]],
  ])("refuses a name that %s already has", (_owner, name, before) => {
    const named = { ...HAIRLINE, name: `'${name}'` };
    const { status, stderr } = scan("faint.tsx", FAINT_TEXT, rulesOf(...before, named));
    expect(stderr).toContain(
      `rule ${before.length + 1} in devtools/conformance.rules.mjs is named ${name}, and a rule`,
    );
    expect(status).toBe(2);
  });
});
