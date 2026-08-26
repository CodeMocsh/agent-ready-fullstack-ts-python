import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { gate } from "./harness";

const OFFENDING_MARKUP = [
  "import { useEffect, useState } from 'react';",
  "export function Bad() {",
  "  const [items, setItems] = useState<string[]>([]);",
  "  useEffect(() => {",
  "    fetch('/api/items').then(async (response) => setItems(await response.json()));",
  "  }, []);",
  "  return (",
  "    <div className='bg-blue-500 border-[#1a1a1a] text-[13px] p-[7px] bg-primary/80'>",
  "      <span className='bg-[rebeccapurple] stroke-2' strokeWidth={1.5} />",
  "      <p style={{ fontSize: 13, color: 'rgb(1,2,3)' }}>{items.length}</p>",
  "    </div>",
  "  );",
  "}",
];

const OFFENDING_RULES = [
  "arbitrary-spacing",
  "arbitrary-type",
  "effect-data",
  "inline-type-declaration",
  "magic-presentation-prop",
  "named-colour",
  "palette-utility",
  "raw-colour",
  "raw-stroke",
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
  "      <p className='text-muted-foreground border-t-border text-sm/6 leading-tight'>{fontSize}</p>",
  "      <svg className='stroke-muted-foreground stroke-[length:var(--icon-stroke)]'>",
  "        <path className='stroke-(length:--icon-stroke)' strokeWidth={ICON_STROKE} />",
  "      </svg>",
  "    </div>",
  "  );",
  "}",
];

const conformance = gate("conformance.mjs", "conformance");

function scan(name: string, lines: string[]) {
  const { status, stdout, lines: reported } = conformance.run(name, lines);
  const rules = reported.map((line) => line.split(/\s{2,}/)[1]);
  return { status, stdout, rules: [...new Set(rules)].sort() };
}

describe("the conformance gate", () => {
  beforeEach(conformance.open);

  afterEach(conformance.close);

  it("fires every markup rule on markup that breaks all of them", () => {
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

  it("counts exactly the checks this table demonstrates", () => {
    const demonstrated = new Set([...OFFENDING_RULES, ...STYLESHEET_RULES]);
    const { stdout } = scan("good.tsx", CLEAN);
    expect(stdout).toContain(`${demonstrated.size} checks`);
  });
});
