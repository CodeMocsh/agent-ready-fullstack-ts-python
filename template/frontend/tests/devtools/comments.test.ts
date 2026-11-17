import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { gate } from "./harness";

const OFFENDERS = [
  '/// <reference types="vite/client" />',
  "const trailing = 1; // a note",
  "/* a block comment */",
  "/** jsdoc, and neither half here is published */",
  "// biome-ignore lint/suspicious/noExplicitAny: because",
  "// @ts-expect-error",
  "export { trailing };",
];

const OFFENDING_LINES = [2, 3, 4, 5, 6];

const CLEAN = [
  '/// <reference types="vite/client" />',
  'const url = "https://example.com//double";',
  "const protocol = /https?:\\/\\//;",
  "const escaped = /a\\/\\/b/;",
  "const slashClass = /[//]/;",
  "const slashOrStar = /[/*]/;",
  "const separators = /[/\\\\]/;",
  "const usage = `--exclude <pattern>   trailing /** is optional`;",
  "const divided = 10 / 2 / 1;",
  "const afterCall = Math.max(1, 2) / 2;",
  "const afterIndex = [4, 2][0] / 2;",
  "function probe(value: string) {",
  "  return /[/*]/.test(value);",
  "}",
  "export { url, protocol, escaped, slashClass, slashOrStar, separators };",
  "export { usage, divided, afterCall, afterIndex, probe };",
];

const INTERPOLATION = "$" + "{";

const AFTER_A_KEYWORD = [
  'import"./styles.css"; // after an import',
  "function quoted() {",
  '  return"a "; // after a return',
  "}",
  `const nested = \`${INTERPOLATION}quoted() ? \`http://x\` : ''}\`;`,
  "export { nested };",
];

const AFTER_A_KEYWORD_LINES = [1, 3];

const AFTER_A_REGEX = [
  "const slashOrStar = /[/*]/;",
  "// an unterminated block comment would swallow this one",
  "const divided = 10 / 2; // and this one",
  "export { slashOrStar, divided };",
];

const AFTER_A_REGEX_LINES = [2, 3];

const comments = gate("comments.mjs", "comments");

function scan(name: string, lines: string[]) {
  const { status, path, lines: reported } = comments.run(name, lines);
  return {
    status,
    reported: reported.map((line) => Number.parseInt(line.slice(path.length + 1), 10)),
  };
}

describe("the comment gate", () => {
  beforeEach(comments.open);

  afterEach(comments.close);

  it("reports every comment, and every suppression spelled as one", () => {
    const { status, reported } = scan("offenders.ts", OFFENDERS);
    expect(reported).toEqual(OFFENDING_LINES);
    expect(status).toBe(1);
  });

  it("reports nothing in a file whose slashes are all code", () => {
    const { status, reported } = scan("clean.ts", CLEAN);
    expect(reported).toEqual([]);
    expect(status).toBe(0);
  });

  it("still sees the comments that follow a regex holding a comment marker", () => {
    const { status, reported } = scan("after.ts", AFTER_A_REGEX);
    expect(reported).toEqual(AFTER_A_REGEX_LINES);
    expect(status).toBe(1);
  });

  it("still sees the comments after a string that follows a keyword, and none in a nested template", () => {
    const { status, reported } = scan("keyword.ts", AFTER_A_KEYWORD);
    expect(reported).toEqual(AFTER_A_KEYWORD_LINES);
    expect(status).toBe(1);
  });
});
