#!/usr/bin/env node
import { readFileSync } from "node:fs";
import { count, excluded, fail, ordering, section, sourceFiles } from "./gate.mjs";
import { lineOf, regions } from "./scan.mjs";

const SOURCE = /\.(?:ts|tsx|mts|mjs|js|jsx|css)$/;

const USAGE = `usage: node devtools/comments.mjs <paths...> [options]

  --exclude <pattern>   skip a path; repeatable, and trailing /** is optional

Reads comments.exclude from package.json as well. See AGENTS.md for
what this refuses, and for the two things it deliberately does not read.`;

function isDirective(text, index) {
  const lineStart = text.lastIndexOf("\n", index) + 1;
  if (text.slice(lineStart, index).trim() !== "") return false;
  return text.startsWith("///", index);
}

function violation(file, text, region) {
  const first = text.slice(region.index, region.end).split("\n")[0].trim();
  return { file, line: lineOf(text, region.index), text: first };
}

function offenders(file) {
  const text = readFileSync(file, "utf8");
  return regions(text)
    .filter((region) => region.comment && !isDirective(text, region.index))
    .map((region) => violation(file, text, region));
}

function configuredExcludes() {
  const exclude = section("comments").exclude ?? [];
  if (!Array.isArray(exclude)) fail("comments.exclude in package.json must be an array.");
  return exclude;
}

function parseArgs(argv) {
  const out = { paths: [], exclude: [] };
  for (let index = 0; index < argv.length; index++) {
    const arg = argv[index];
    if (arg === "--help" || arg === "-h") {
      process.stdout.write(`${USAGE}\n`);
      process.exit(0);
    } else if (arg === "--exclude") out.exclude.push(argv[++index]);
    else if (arg.startsWith("-")) fail(`unknown option ${arg}\n\n${USAGE}`);
    else out.paths.push(arg);
  }
  if (out.paths.length === 0) fail(`no paths given\n\n${USAGE}`);
  return out;
}

function report(found) {
  for (const violation of found) {
    process.stderr.write(`${violation.file}:${violation.line}  ${violation.text}\n`);
  }
  process.stderr.write(
    `\nFAIL: ${count(found.length, "comment")} above. AGENTS.md bans them, and the\n` +
      "rule is not about tidiness: an explanation beside the code is the copy that\n" +
      "goes stale silently. Rationale belongs in the commit message, a decision in\n" +
      "docs/adr/, and a contract in a name or a type.\n\n" +
      "A suppression is the half most worth refusing. `biome-ignore` and\n" +
      "`@ts-expect-error` are threshold decisions taken silently at the point of\n" +
      "pain; make it a fix in the code, or a reviewable line in biome.json or\n" +
      "tsconfig.json. Vendored and generated files belong in comments.exclude in\n" +
      "package.json, never in a wider rule.\n",
  );
}

const { paths, exclude } = parseArgs(process.argv.slice(2));
const patterns = [...configuredExcludes(), ...exclude];
const files = sourceFiles(paths, {
  matches: (name) => SOURCE.test(name),
  skipped: (path) => excluded(path, patterns),
});
const found = files.flatMap(offenders).sort(ordering);

if (found.length > 0) {
  report(found);
  process.exit(1);
}

process.stdout.write(`comments: ${count(files.length, "file")}, none carrying one.\n`);
