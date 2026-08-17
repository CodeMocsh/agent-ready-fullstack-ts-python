#!/usr/bin/env node
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const SOURCE = /\.(?:ts|tsx|mts|mjs|js|jsx|css)$/;
const SKIP_DIRS = new Set(["node_modules", "dist", "build", "coverage", ".git"]);

const USAGE = `usage: node devtools/comments.mjs <paths...> [options]

  --exclude <pattern>   skip a path; repeatable, and trailing /** is optional

Reads comments.exclude from package.json as well. See docs/agent-tooling.md for
what this refuses, and for the two things it deliberately does not read.`;

const REGIONS = [
  { start: '"', end: '"', escapes: true, comment: false },
  { start: "'", end: "'", escapes: true, comment: false },
  { start: "`", end: "`", escapes: true, comment: false },
  { start: "//", end: "\n", escapes: false, comment: true },
  { start: "/*", end: "*/", escapes: false, comment: true },
];

const TAIL_LENGTH = 16;
const DIVISION_FOLLOWS = /[\w$)\]]$/;
const KEYWORD_FOLLOWS =
  /\b(?:await|case|delete|do|else|in|instanceof|new|of|return|throw|typeof|void|yield)$/;

function fail(message) {
  process.stderr.write(`error: ${message}\n`);
  process.exit(2);
}

function opener(text, index) {
  return REGIONS.find((region) => text.startsWith(region.start, index)) ?? null;
}

function regionEnd(text, index, opened) {
  let cursor = index + opened.start.length;
  while (cursor < text.length) {
    if (opened.escapes && text[cursor] === "\\") cursor += 2;
    else if (text.startsWith(opened.end, cursor)) return cursor + opened.end.length;
    else cursor++;
  }
  return text.length;
}

function lineOf(text, index) {
  return text.slice(0, index).split("\n").length;
}

function isDirective(text, index) {
  const lineStart = text.lastIndexOf("\n", index) + 1;
  if (text.slice(lineStart, index).trim() !== "") return false;
  return text.startsWith("///", index);
}

function startsARegex(tail) {
  return tail === "" || !DIVISION_FOLLOWS.test(tail) || KEYWORD_FOLLOWS.test(tail);
}

function regexEnd(text, index) {
  let cursor = index + 1;
  let inClass = false;
  while (cursor < text.length) {
    const char = text[cursor];
    if (char === "\\") cursor += 2;
    else if (char === "\n") return index + 1;
    else if (char === "/" && !inClass) return cursor + 1;
    else {
      if (char === "[") inClass = true;
      else if (char === "]") inClass = false;
      cursor++;
    }
  }
  return index + 1;
}

function nextTail(tail, char) {
  return char.trim() === "" ? tail : `${tail}${char}`.slice(-TAIL_LENGTH);
}

function violation(file, text, index, end) {
  const first = text.slice(index, end).split("\n")[0].trim();
  return { file, line: lineOf(text, index), text: first };
}

function regionStep(text, index, opened, tail) {
  return { index: regionEnd(text, index, opened), tail: opened.comment ? tail : opened.end };
}

function codeStep(text, index, tail) {
  if (text[index] === "/" && startsARegex(tail)) {
    return { index: regexEnd(text, index), tail: "/" };
  }
  const width = text[index] === "\\" ? 2 : 1;
  return { index: index + width, tail: nextTail(tail, text[index]) };
}

function offenders(file) {
  const text = readFileSync(file, "utf8");
  const found = [];
  let index = 0;
  let tail = "";
  while (index < text.length) {
    const opened = opener(text, index);
    const step =
      opened === null ? codeStep(text, index, tail) : regionStep(text, index, opened, tail);
    if (opened?.comment && !isDirective(text, index)) {
      found.push(violation(file, text, index, step.index));
    }
    index = step.index;
    tail = step.tail;
  }
  return found;
}

function excluded(path, patterns) {
  return patterns.some((pattern) => {
    const prefix = pattern.replace(/\/\*\*$/, "");
    return path === prefix || path.startsWith(`${prefix}/`);
  });
}

function walk(directory, exclude, found) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const child = join(directory, entry.name);
    if (excluded(child, exclude)) continue;
    if (entry.isDirectory() && !SKIP_DIRS.has(entry.name)) walk(child, exclude, found);
    else if (entry.isFile() && SOURCE.test(entry.name)) found.push(child);
  }
}

function sourceFiles(paths, exclude) {
  const found = [];
  for (const path of paths) {
    if (excluded(path, exclude)) continue;
    if (statSync(path).isDirectory()) walk(path, exclude, found);
    else if (SOURCE.test(path)) found.push(path);
  }
  return found;
}

function configuredExcludes() {
  const file = "package.json";
  if (!existsSync(file)) return [];
  const configured = JSON.parse(readFileSync(file, "utf8")).comments ?? {};
  const exclude = configured.exclude ?? [];
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

function count(total, noun) {
  return `${total} ${noun}${total === 1 ? "" : "s"}`;
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
const files = sourceFiles(paths, [...configuredExcludes(), ...exclude]);
const found = files
  .flatMap(offenders)
  .sort((one, other) =>
    one.file === other.file ? one.line - other.line : one.file.localeCompare(other.file),
  );

if (found.length > 0) {
  report(found);
  process.exit(1);
}

process.stdout.write(`comments: ${count(files.length, "file")}, none carrying one.\n`);
