#!/usr/bin/env node
import { existsSync } from "node:fs";
import {
  assertResolves,
  count,
  excluded,
  fail,
  nonBlankLines,
  ordering,
  section,
  sourceFiles,
} from "./gate.mjs";

const SOURCE = /\.(?:ts|tsx|mts|cts|mjs|js|jsx)$/;

const USAGE = `usage: node devtools/file-length.mjs <paths...>

Reads complexity.maxFileLines, complexity.overCap and complexity.exclude from
package.json. See AGENTS.md for what to do when a file crosses the cap.`;

function settings() {
  const { maxFileLines, overCap, exclude } = section("complexity");
  if (!Number.isInteger(maxFileLines) || maxFileLines < 1) {
    fail("complexity.maxFileLines in package.json must be a positive integer.");
  }
  if (!Array.isArray(overCap)) fail("complexity.overCap in package.json must be an array.");
  if (!Array.isArray(exclude)) fail("complexity.exclude in package.json must be an array.");
  assertResolves(exclude, "complexity.exclude");
  return { cap: maxFileLines, overCap, exclude };
}

function parseArgs(argv) {
  if (argv.includes("--help") || argv.includes("-h")) {
    process.stdout.write(`${USAGE}\n`);
    process.exit(0);
  }
  const unknown = argv.find((arg) => arg.startsWith("-"));
  if (unknown) fail(`unknown option ${unknown}\n\n${USAGE}`);
  if (argv.length === 0) fail(`no paths given\n\n${USAGE}`);
  return argv;
}

function unneeded(entry, measured, cap) {
  const lines = measured.get(entry);
  if (lines === undefined) {
    return [existsSync(entry) ? `${entry} (not a file this gate reads)` : `${entry} (gone)`];
  }
  return lines > cap ? [] : [`${entry} (${lines} lines, not past the cap of ${cap})`];
}

function reportOverlong(overlong, cap) {
  for (const found of overlong) {
    process.stderr.write(`${found.file}: ${found.lines} lines\n`);
  }
  process.stderr.write(
    `\nFAIL: ${count(overlong.length, "file")} above ${cap} non-blank lines. Split by what the\n` +
      "parts require of a caller, not by size: the seam that matters is the one that is\n" +
      "expensive to get wrong. A file that must stay whole goes in complexity.overCap in\n" +
      "package.json, by name, where a reviewer sees it. Raising the cap covers the next\n" +
      "file to grow past it, and covers it silently.\n",
  );
}

function reportUnneeded(stale) {
  process.stderr.write(
    `\nFAIL: remove these from complexity.overCap in package.json: ${stale.join(", ")}.\n` +
      "The list only ever gets shorter. An entry the cap does not need covers its file\n" +
      "again, silently, the day that file grows back.\n",
  );
}

const paths = parseArgs(process.argv.slice(2));
const { cap, overCap, exclude } = settings();
const files = sourceFiles(paths, {
  matches: (name) => SOURCE.test(name),
  skipped: (path) => excluded(path, exclude),
});
const measured = new Map(files.map((file) => [file, nonBlankLines(file)]));
const overlong = [...measured]
  .map(([file, lines]) => ({ file, lines }))
  .filter((found) => found.lines > cap && !overCap.includes(found.file))
  .sort(ordering);
const stale = overCap.flatMap((entry) => unneeded(entry, measured, cap));

if (overlong.length > 0) reportOverlong(overlong, cap);
if (stale.length > 0) reportUnneeded(stale);
if (overlong.length > 0 || stale.length > 0) process.exit(1);

process.stdout.write(`file length: ${count(files.length, "file")}, none past ${cap}.\n`);
