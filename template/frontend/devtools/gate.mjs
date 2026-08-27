import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const SKIP_DIRS = new Set(["node_modules", "dist", "build", "coverage", ".git"]);

export function fail(message) {
  process.stderr.write(`error: ${message}\n`);
  process.exit(2);
}

export function count(total, noun) {
  return `${total} ${noun}${total === 1 ? "" : "s"}`;
}

export function ordering(one, other) {
  return one.file === other.file ? one.line - other.line : one.file.localeCompare(other.file);
}

export function excluded(path, patterns) {
  return patterns.some((pattern) => {
    const prefix = pattern.replace(/\/\*\*$/, "");
    return path === prefix || path.startsWith(`${prefix}/`);
  });
}

export function section(key) {
  const file = "package.json";
  if (!existsSync(file)) return {};
  return JSON.parse(readFileSync(file, "utf8"))[key] ?? {};
}

export function assertResolves(paths, setting) {
  const missing = paths.filter((pattern) => !existsSync(pattern.replace(/\/\*\*$/, "")));
  if (missing.length === 0) return;
  fail(
    `${setting} names ${missing.join(", ")}, which is not in the tree.\n` +
      "An entry matching nothing reads as a rule someone considered, and is a hole held\n" +
      "open for whatever lands at that path next: the file arrives, it is exempt on\n" +
      "arrival, and no check says so. Correct the path, or drop the entry.",
  );
}

function walk(directory, selection, found) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const child = join(directory, entry.name);
    if (selection.skipped(child)) continue;
    if (entry.isDirectory() && !SKIP_DIRS.has(entry.name)) walk(child, selection, found);
    else if (entry.isFile() && selection.matches(entry.name)) found.push(child);
  }
}

export function sourceFiles(paths, selection) {
  const found = [];
  for (const path of paths) {
    if (selection.skipped(path)) continue;
    if (statSync(path).isDirectory()) walk(path, selection, found);
    else if (selection.matches(path)) found.push(path);
  }
  return found;
}
