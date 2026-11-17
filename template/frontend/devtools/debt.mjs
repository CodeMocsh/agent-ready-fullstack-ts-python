import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { fail } from "./gate.mjs";

const KEYS = "files,rules";

function isPlainObject(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function refuser(path) {
  return (problem) =>
    fail(
      `${path} ${problem}.\n` +
        'The conformance debt is { "rules": [rule names], "files": { path: { rule: count } } },\n' +
        "and each count is a whole number above zero for a rule that rules names.",
    );
}

function assertEntries(files, rules, refuse) {
  for (const [file, entries] of Object.entries(files)) {
    if (!isPlainObject(entries)) refuse(`has an entry for ${file} that is not an object`);
    for (const [rule, owed] of Object.entries(entries)) {
      if (!rules.includes(rule)) refuse(`counts ${rule} for ${file}, and rules does not name it`);
      if (!(Number.isInteger(owed) && owed > 0)) refuse(`counts ${owed} ${rule} for ${file}`);
    }
  }
}

function assertDebt(debt, path) {
  const refuse = refuser(path);
  if (!isPlainObject(debt) || Object.keys(debt).sort().join(",") !== KEYS) {
    refuse("does not hold exactly rules and files");
  }
  const { rules, files } = debt;
  if (!(Array.isArray(rules) && rules.every((rule) => typeof rule === "string"))) {
    refuse("has rules that are not a list of names");
  }
  if (new Set(rules).size !== rules.length) refuse("names a rule twice in rules");
  if (!isPlainObject(files)) refuse("has files that are not an object");
  assertEntries(files, rules, refuse);
}

function read(path) {
  if (path === null || !existsSync(path)) return { rules: [], files: {} };
  let debt;
  try {
    debt = JSON.parse(readFileSync(path, "utf8"));
  } catch (error) {
    fail(`${path} is not JSON: ${error.message}`);
  }
  assertDebt(debt, path);
  return debt;
}

function byKey([one], [other]) {
  if (one === other) return 0;
  return one < other ? -1 : 1;
}

function serialized(debt) {
  const files = Object.entries(debt.files)
    .sort(byKey)
    .map(([file, entries]) => [file, Object.fromEntries(Object.entries(entries).sort(byKey))]);
  const rules = [...debt.rules].sort();
  return `${JSON.stringify({ rules, files: Object.fromEntries(files) }, null, 2)}\n`;
}

function keyOf(file, rule) {
  return `${file}\n${rule}`;
}

function counted(violations) {
  const counts = new Map();
  for (const { file, rule } of violations) {
    counts.set(keyOf(file, rule), foundIn(counts, file, rule) + 1);
  }
  return counts;
}

function foundIn(counts, file, rule) {
  const key = keyOf(file, rule);
  return counts.has(key) ? counts.get(key) : 0;
}

function owedBy(debt, file, rule) {
  return Object.hasOwn(debt.files, file) && Object.hasOwn(debt.files[file], rule)
    ? debt.files[file][rule]
    : 0;
}

function recorded(debt, counts, rules) {
  const fresh = rules.filter((rule) => !debt.rules.includes(rule));
  const files = structuredClone(debt.files);
  for (const [key, found] of counts) {
    const [file, rule] = key.split("\n");
    if (fresh.includes(rule)) files[file] = { ...files[file], [rule]: found };
  }
  return { next: { rules: [...debt.rules, ...fresh], files }, fresh };
}

function staleFile(file) {
  return existsSync(file)
    ? { label: file, reason: "not a file this gate reads" }
    : { label: file, reason: "gone; if you renamed it, put its counts under the new path" };
}

function looseness(debt, counts, scanned, rules) {
  const loose = debt.rules
    .filter((rule) => !rules.includes(rule))
    .map((rule) => ({ label: `rule ${rule}`, reason: "no such rule" }));
  for (const [file, entries] of Object.entries(debt.files)) {
    if (!scanned.has(file)) {
      loose.push(staleFile(file));
      continue;
    }
    for (const [rule, owed] of Object.entries(entries)) {
      const found = foundIn(counts, file, rule);
      if (rules.includes(rule) && found < owed) loose.push({ file, rule, owed, found });
    }
  }
  return loose;
}

function tightened(debt, counts, scanned, rules) {
  const files = {};
  for (const [file, entries] of Object.entries(debt.files)) {
    if (!scanned.has(file)) continue;
    const kept = Object.entries(entries)
      .filter(([rule]) => rules.includes(rule))
      .map(([rule, owed]) => [rule, Math.min(owed, foundIn(counts, file, rule))])
      .filter(([, owed]) => owed > 0);
    if (kept.length > 0) files[file] = Object.fromEntries(kept);
  }
  const findsNothing = (rule) => ![...counts.keys()].some((key) => key.endsWith(`\n${rule}`));
  const named = rules.filter((rule) => debt.rules.includes(rule) || findsNothing(rule));
  return { rules: named, files };
}

export function settled(violations, { path, scanned, rules, record, tighten }) {
  const known = path !== null && existsSync(path);
  const counts = counted(violations);
  const { next, fresh } = record
    ? recorded(read(path), counts, rules)
    : { next: read(path), fresh: [] };
  const loose = looseness(next, counts, scanned, rules);
  const debt = tighten ? tightened(next, counts, scanned, rules) : next;
  const named = known ? debt.rules.filter((rule) => !next.rules.includes(rule)) : [];
  const text = serialized(debt);
  if ((record || tighten) && (known ? readFileSync(path, "utf8") !== text : record)) {
    writeFileSync(path, text);
  }
  const past = violations.filter(
    ({ file, rule }) => foundIn(counts, file, rule) > owedBy(debt, file, rule),
  );
  const unrecorded = known ? past.filter(({ rule }) => !debt.rules.includes(rule)) : [];
  return {
    known: known || record,
    over: past.filter((violation) => !unrecorded.includes(violation)),
    unrecorded,
    loose: tighten ? [] : loose,
    tightenedFrom: tighten ? loose : [],
    recorded: fresh,
    named,
    owed: violations.length - past.length,
  };
}

function entryLine(entry) {
  if (Object.hasOwn(entry, "reason")) return `${entry.label}  (${entry.reason})`;
  return `${entry.file}  ${entry.rule}  ${entry.owed} -> ${entry.found}`;
}

export function reportLoose(loose, path) {
  for (const entry of loose) process.stderr.write(`${entryLine(entry)}\n`);
  process.stderr.write(
    `\nFAIL: ${path} owes more than the tree holds. A count left above the tree lets\n` +
      "a finding come back unseen. `pnpm lint` lowers each count and deletes each entry\n" +
      `listed above. Commit ${path}.\n`,
  );
}

export function reportTightened(tightenedFrom, path) {
  for (const entry of tightenedFrom) process.stdout.write(`${entryLine(entry)}\n`);
  process.stdout.write(`conformance debt lowered -> ${path}. Commit it.\n`);
}
