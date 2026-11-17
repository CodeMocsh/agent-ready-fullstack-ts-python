import { writeFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { gate, NO_PROJECT_RULES } from "./harness";

const THEME = [
  "@theme inline {",
  "  --color-muted-foreground: var(--muted-foreground);",
  "  --text-2xs: 0.625rem;",
  "  --font-heading: var(--font-sans);",
  "  --font-weight-hero: 650;",
  "}",
  "@theme {",
  "  --color-ink: var(--foreground);",
  "}",
  "@utility meta {",
  "  @apply text-sm text-muted-foreground;",
  "}",
  "@utility leading-ui {",
  "  @apply leading-tight;",
  "}",
  "@utility shadow-card {",
  "  @apply shadow-sm rounded-lg;",
  "}",
].join("\n");

const INTERPOLATION = "$" + "{";

const RESET_SPACING = "@theme {\n  --spacing: initial;\n  --spacing-row: 1rem;\n}\n";

const conformance = gate("conformance.mjs", "composition");

function marked(classes: string) {
  return ["export function Probe() {", `  return <p className='${classes}'>probe</p>;`, "}"];
}

function found(source: string[], args: string[] = [], name = "probe.tsx") {
  const { status, lines } = conformance.run(name, source, NO_PROJECT_RULES, args);
  return {
    status,
    found: lines.map((line) =>
      line
        .split(/\s{2,}/)
        .slice(1)
        .join(" "),
    ),
  };
}

function withTheme(theme: string) {
  return () => {
    conformance.open();
    writeFileSync(conformance.at("src/index.css"), theme);
  };
}

describe("the composition rules on text", () => {
  beforeEach(withTheme(THEME));

  afterEach(conformance.close);

  it("refuses each kind of text class outside a role, from every @theme block", () => {
    const classes = "text-sm text-2xs text-muted-foreground text-ink font-semibold font-heading";
    const { status, found: hits } = found(
      marked(`${classes} leading-tight tracking-wide uppercase`),
    );
    expect(hits).toHaveLength(9);
    expect(new Set(hits.map((hit) => hit.split(" ")[0]))).toEqual(new Set(["text-outside-a-role"]));
    expect(status).toBe(1);
  });

  it("counts a class behind a variant or an important mark", () => {
    const { found: hits } = found(marked("hover:text-muted-foreground md:text-lg !font-bold"));
    expect(hits).toEqual([
      "text-outside-a-role text-muted-foreground",
      "text-outside-a-role text-lg",
      "text-outside-a-role font-bold",
    ]);
  });

  it("leaves layout text classes, colours the theme does not name, and theme utilities alone", () => {
    const classes = "text-left text-center text-wrap text-ellipsis text-balance text-current";
    const { status, found: hits } = found(marked(`${classes} meta leading-ui shadow-card`));
    expect(hits).toEqual([]);
    expect(status).toBe(0);
  });

  it("reads class strings anywhere, inside an interpolation too, and never an identifier", () => {
    const { lines } = conformance.run("probe.tsx", [
      "const TONE = { quiet: 'text-muted-foreground' };",
      "const rounded = (value: number) => Math.round(value);",
      "const paint = { shadow: rounded(1) };",
      "export function Probe({ on }: { on: boolean }) {",
      `  const tone = \`p-2 ${INTERPOLATION}on`,
      `    ? \`x font-bold ${INTERPOLATION}TONE.quiet}\``,
      "    : 'x shadow-sm'} rounded`;",
      "  return <Card className={tone} depth={paint.shadow} />;",
      "}",
    ]);
    expect(lines.map((line) => line.replace(/^.*probe\.tsx/, "probe.tsx"))).toEqual([
      "probe.tsx:1  text-outside-a-role  text-muted-foreground",
      "probe.tsx:6  text-outside-a-role  font-bold",
      "probe.tsx:7  surface-outside-a-primitive  shadow-sm",
      "probe.tsx:7  surface-outside-a-primitive  rounded",
    ]);
  });

  it("reads no import path, and no apostrophe in markup as a string", () => {
    const { found: hits } = found([
      "import { Card } from './rounded-card';",
      "export function Probe() {",
      "  return <p>Don't round it. <button type='button'>{Card.name}</button><button-group /></p>;",
      "}",
    ]);
    expect(hits).toEqual(["element-outside-a-primitive <button"]);
  });
});

describe("the composition rules on elements and surfaces", () => {
  beforeEach(withTheme(THEME));

  afterEach(conformance.close);

  it("refuses a raw button and a raw input in markup, and not in a string", () => {
    const { found: hits } = found([
      "const SAYS = '<button> and <input> are words here';",
      "export function Probe() {",
      "  return <form><input name='q' /><button type='submit'>{SAYS}</button></form>;",
      "}",
    ]);
    expect(hits).toEqual([
      "element-outside-a-primitive <input",
      "element-outside-a-primitive <button",
    ]);
  });

  it("refuses a radius or a shadow, and leaves a filter alone", () => {
    const { found: hits } = found(
      marked("rounded rounded-lg rounded-t-md shadow shadow-sm drop-shadow-md inset-shadow-xs"),
    );
    expect(hits).toEqual([
      "surface-outside-a-primitive rounded",
      "surface-outside-a-primitive rounded-lg",
      "surface-outside-a-primitive rounded-t-md",
      "surface-outside-a-primitive shadow",
      "surface-outside-a-primitive shadow-sm",
    ]);
  });

  it("lets a primitive set its own text, radius and shadow, and holds it to the theme", () => {
    const source = [
      "export function Probe() {",
      "  return <button className='rounded-lg shadow-sm text-sm bg-blue-500'>go</button>;",
      "}",
    ];
    const { found: hits } = found(source, ["--primitive", conformance.at("tree/probe.tsx")]);
    expect(hits).toEqual(["palette-utility bg-blue-500"]);
  });

  it("reads the folders of the primitives from package.json", () => {
    const kit = conformance.at("tree/kit");
    const config = { conformance: { primitives: [`${kit}/**`] } };
    writeFileSync(conformance.at("package.json"), JSON.stringify(config));
    const { found: hits } = found(marked("rounded-lg text-sm"), [], "kit/probe.tsx");
    expect(hits).toEqual([]);
  });

  it("lets conformance.allow permit one class", () => {
    const { found: hits } = found(marked("rounded-full"), ["--allow", "rounded-full"]);
    expect(hits).toEqual([]);
  });
});

describe("the composition rule on spacing", () => {
  beforeEach(conformance.open);

  afterEach(conformance.close);

  it("stays off while the theme keeps the numeric scale", () => {
    writeFileSync(conformance.at("src/index.css"), "@theme {\n  --spacing-row: 1rem;\n}\n");
    const { found: hits } = found(marked("p-7 gap-0 px-1.5"));
    expect(hits).toEqual([]);
  });

  it("turns on when the theme resets every spacing key", () => {
    writeFileSync(conformance.at("src/index.css"), "@theme {\n  --spacing-*: initial;\n}\n");
    const { found: hits } = found(marked("p-7"));
    expect(hits).toEqual(["spacing-off-the-scale p-7"]);
  });

  it("stays off when the reset is in a comment", () => {
    writeFileSync(conformance.at("src/index.css"), "@theme {\n  /* --spacing: initial; */\n}\n");
    const { found: hits } = found(marked("p-7"));
    expect(hits).toEqual([]);
  });

  it("refuses a step Tailwind drops once the theme resets the scale", () => {
    writeFileSync(conformance.at("src/index.css"), RESET_SPACING);
    const { found: hits } = found(marked("p-0 gap-7 px-1.5 -mt-2 space-x-4 pbs-4"));
    expect(hits).toEqual([
      "spacing-off-the-scale p-0",
      "spacing-off-the-scale gap-7",
      "spacing-off-the-scale px-1.5",
      "spacing-off-the-scale -mt-2",
      "spacing-off-the-scale space-x-4",
      "spacing-off-the-scale pbs-4",
    ]);
  });

  it("keeps each step Tailwind still generates once the theme resets the scale", () => {
    writeFileSync(conformance.at("src/index.css"), RESET_SPACING);
    const named = "p-row gap-x-row -m-row space-y-row";
    const { found: hits, status } = found(
      marked(`${named} p-px m-auto mx-auto p-(--inset) space-x-reverse`),
    );
    expect(hits).toEqual([]);
    expect(status).toBe(0);
  });
});
