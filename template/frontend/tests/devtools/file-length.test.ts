import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

const SCRIPT = resolve("devtools/file-length.mjs");

const CAP = 500;

function statements(total: number) {
  return Array.from({ length: total }, (_, index) => `export const value${index} = ${index};`);
}

function component(total: number) {
  const rows = Array.from({ length: total - 6 }, (_, index) => `      <li>row ${index}</li>`);
  return ["export function Long() {", "  return (", "    <ul>", ...rows, "    </ul>", "  );", "}"];
}

function withBlanks(total: number, blank: number) {
  return [...statements(total - blank), ...Array.from({ length: blank }, () => "")];
}

let project = "";

function write(path: string, lines: string[]) {
  writeFileSync(join(project, path), `${lines.join("\n")}\n`);
}

function run(overCap: string[] = []) {
  const complexity = { maxFileLines: CAP, overCap, exclude: [] };
  writeFileSync(join(project, "package.json"), JSON.stringify({ complexity }));
  const finished = spawnSync("node", [SCRIPT, "src"], { cwd: project, encoding: "utf8" });
  return {
    status: finished.status,
    stderr: finished.stderr,
    reported: finished.stderr
      .split("\n")
      .filter((line) => line.startsWith("src/"))
      .map((line) => line.split(":")[0]),
  };
}

describe("the file length gate", () => {
  beforeEach(() => {
    project = mkdtempSync(join(tmpdir(), "file-length-"));
    mkdirSync(join(project, "src"));
  });

  afterEach(() => rmSync(project, { recursive: true, force: true }));

  it("refuses a file past the cap, and passes one under it", () => {
    write("src/long.ts", statements(CAP + 1));
    write("src/short.ts", statements(CAP - 1));
    const { status, reported } = run();
    expect(reported).toEqual(["src/long.ts"]);
    expect(status).toBe(1);
  });

  it("does not count blank lines towards the cap", () => {
    write("src/spaced.ts", withBlanks(CAP + 1, 200));
    const { status, reported } = run();
    expect(reported).toEqual([]);
    expect(status).toBe(0);
  });

  it("refuses a file whose length is JSX like any other", () => {
    write("src/long.tsx", component(CAP + 1));
    const { status, reported } = run();
    expect(reported).toEqual(["src/long.tsx"]);
    expect(status).toBe(1);
  });

  it("skips a file named in overCap", () => {
    write("src/long.tsx", component(CAP + 1));
    const { status, reported } = run(["src/long.tsx"]);
    expect(reported).toEqual([]);
    expect(status).toBe(0);
  });

  it("refuses an overCap entry for a file that is gone, at the cap, or never read", () => {
    write("src/short.ts", statements(CAP));
    write("outside.ts", statements(CAP + 1));
    const { status, stderr } = run(["src/short.ts", "src/split.ts", "outside.ts"]);
    expect(stderr).toContain(`src/short.ts (${CAP} lines, not past the cap of ${CAP})`);
    expect(stderr).toContain("src/split.ts (gone)");
    expect(stderr).toContain("outside.ts (not a file this gate reads)");
    expect(status).toBe(1);
  });

  it("reports a file past the cap even when an overCap entry is stale", () => {
    write("src/long.ts", statements(CAP + 1));
    const { status, reported, stderr } = run(["src/split.ts"]);
    expect(reported).toEqual(["src/long.ts"]);
    expect(stderr).toContain("src/split.ts (gone)");
    expect(status).toBe(1);
  });
});
