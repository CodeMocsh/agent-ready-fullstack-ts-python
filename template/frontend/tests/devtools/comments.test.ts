import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

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

const AFTER_A_REGEX = [
  "const slashOrStar = /[/*]/;",
  "// an unterminated block comment would swallow this one",
  "const divided = 10 / 2; // and this one",
  "export { slashOrStar, divided };",
];

const AFTER_A_REGEX_LINES = [2, 3];

let directory = "";

function scan(name: string, lines: string[]) {
  const path = join(directory, name);
  writeFileSync(path, `${lines.join("\n")}\n`);
  const finished = spawnSync("node", ["devtools/comments.mjs", directory], {
    encoding: "utf8",
  });
  const reported = finished.stderr
    .split("\n")
    .filter((line) => line.startsWith(`${path}:`))
    .map((line) => Number.parseInt(line.slice(path.length + 1), 10));
  return { status: finished.status, reported };
}

describe("the comment gate", () => {
  beforeEach(() => {
    directory = mkdtempSync(join(tmpdir(), "comments-"));
  });

  afterEach(() => {
    rmSync(directory, { recursive: true, force: true });
  });

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
});
