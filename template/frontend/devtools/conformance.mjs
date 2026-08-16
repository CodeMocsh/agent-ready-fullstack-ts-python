#!/usr/bin/env node
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const DEFAULTS = {
  themeFiles: ["src/index.css"],
  allow: [],
  exclude: [],
};

const SOURCE = /\.(?:ts|tsx|css)$/;
const GENERATED = /\.(?:d|test|spec)\.(?:ts|tsx)$/;
const MARKUP = /\.tsx?$/;
const STYLESHEET = /\.css$/;
const SKIP_DIRS = new Set(["node_modules", "dist", "build", "coverage", ".git"]);

const USAGE = `usage: node devtools/conformance.mjs <paths...> [options]

  --exclude <pattern>   skip a path; repeatable, and trailing /** is optional
  --allow <text>        permit one exact match; repeatable
  --theme-file <path>   a file allowed to define the theme; repeatable

See docs/agent-tooling.md for what each check enforces and where it came from.`;

const NAMED_COLOURS = [
  "aliceblue|antiquewhite|aqua|aquamarine|azure|beige|bisque|black|blanchedalmond|blue",
  "blueviolet|brown|burlywood|cadetblue|chartreuse|chocolate|coral|cornflowerblue|cornsilk",
  "crimson|cyan|darkblue|darkcyan|darkgoldenrod|darkgray|darkgreen|darkgrey|darkkhaki",
  "darkmagenta|darkolivegreen|darkorange|darkorchid|darkred|darksalmon|darkseagreen",
  "darkslateblue|darkslategray|darkslategrey|darkturquoise|darkviolet|deeppink|deepskyblue",
  "dimgray|dimgrey|dodgerblue|firebrick|floralwhite|forestgreen|fuchsia|gainsboro|ghostwhite",
  "gold|goldenrod|gray|greenyellow|green|grey|honeydew|hotpink|indianred|indigo|ivory|khaki",
  "lavenderblush|lavender|lawngreen|lemonchiffon|lightblue|lightcoral|lightcyan",
  "lightgoldenrodyellow|lightgray|lightgreen|lightgrey|lightpink|lightsalmon|lightseagreen",
  "lightskyblue|lightslategray|lightslategrey|lightsteelblue|lightyellow|limegreen|lime",
  "linen|magenta|maroon|mediumaquamarine|mediumblue|mediumorchid|mediumpurple|mediumseagreen",
  "mediumslateblue|mediumspringgreen|mediumturquoise|mediumvioletred|midnightblue|mintcream",
  "mistyrose|moccasin|navajowhite|navy|oldlace|olivedrab|olive|orangered|orange|orchid",
  "palegoldenrod|palegreen|paleturquoise|palevioletred|papayawhip|peachpuff|peru|pink|plum",
  "powderblue|purple|rebeccapurple|red|rosybrown|royalblue|saddlebrown|salmon|sandybrown",
  "seagreen|seashell|sienna|silver|skyblue|slateblue|slategray|slategrey|snow|springgreen",
  "steelblue|tan|teal|thistle|tomato|turquoise|violet|wheat|whitesmoke|white|yellowgreen|yellow",
].join("|");

const COLOUR_UTILITIES =
  "bg|text|border|ring|fill|stroke|shadow|from|via|to|outline|decoration|divide|accent|caret|placeholder";

const COLOUR_PROPERTIES = [
  "color|background|background-color|border-color|outline-color|caret-color|accent-color",
  "text-decoration-color|fill|stroke|backgroundColor|borderColor|outlineColor|caretColor",
  "accentColor|textDecorationColor",
].join("|");

const NAMED_COLOUR = new RegExp(
  `(?<![\\w-])(?:${COLOUR_UTILITIES})-\\[(?:${NAMED_COLOURS})\\]` +
    `|(?<=^|[;{\\s"'(])(?:${COLOUR_PROPERTIES})\\s*:\\s*["']?(?:${NAMED_COLOURS})\\b`,
  "g",
);

const ARBITRARY_SPACING =
  /(?<![\w-])-?(?:p[xytrbles]?|m[xytrbles]?|gap(?:-[xy])?|space-[xy])-\[(?![^\]]*--)[^\]]+\]/g;

const RULES = [
  {
    name: "raw-colour",
    files: SOURCE,
    pattern:
      /#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b|(?<![a-zA-Z0-9-])(?:rgba?|hsla?|oklch|oklab|lab|lch)\(/g,
    hint: "a literal colour keeps its value when the theme switches; define it in the @theme block and use the token",
  },
  {
    name: "palette-utility",
    files: SOURCE,
    pattern:
      /(?<![\w-])(?:bg|text|border|ring|fill|stroke|from|via|to|outline|decoration|divide|shadow|accent|caret|placeholder)-(?:slate|gray|grey|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|white|black)\b(?:-\d{2,3})?/g,
    hint: "a fixed palette step is not a token; use bg-background, text-muted-foreground, border-border and their siblings",
  },
  {
    name: "named-colour",
    files: SOURCE,
    pattern: NAMED_COLOUR,
    hint: "a colour keyword is a literal like any other and survives the theme switch; use a token",
  },
  {
    name: "arbitrary-spacing",
    files: SOURCE,
    pattern: ARBITRARY_SPACING,
    hint: "padding, margin and gap come from the spacing scale, which has a step for every legitimate value; an arbitrary one reading a theme variable is fine, an eyeballed 7px is not",
  },
  {
    name: "arbitrary-type",
    files: MARKUP,
    pattern: /(?<![\w-])(?:text|font|leading|tracking)-\[[^\]]+\]/g,
    hint: "an arbitrary value sits outside the scale, where nothing holds it consistent; use text-sm, text-2xl, leading-tight",
  },
  {
    name: "raw-type-declaration",
    files: STYLESHEET,
    pattern: /(?:^|[;{\s])(?:font-family|font-size|line-height|letter-spacing)\s*:/g,
    hint: "declare type in the @theme block and reach for it through a utility, so one scale governs the app",
  },
];

const CHECKS = RULES.length + 2;

const EFFECT = /\buseEffect\s*\(/g;
const DATA_WORK = /\bawait\b|\.then\s*\(|\bfetch\s*\(/;
const EFFECT_HINT =
  "data belongs in a TanStack Query hook; an effect that fetches misses the cache, the loading and error states, and the handlers the tests run against";

const STYLE_ATTRIBUTE = /\bstyle\s*=\s*\{/g;
const INLINE_TYPE = /(?<![\w-])(?:fontFamily|fontSize|lineHeight|letterSpacing)\s*:/g;
const INLINE_TYPE_HINT =
  "an inline font or size bypasses the theme entirely; use a class from the scale";

function excluded(path, patterns) {
  return patterns.some((pattern) => {
    const prefix = pattern.replace(/\/\*\*$/, "");
    return path === prefix || path.startsWith(`${prefix}/`);
  });
}

function measurable(name) {
  return SOURCE.test(name) && !GENERATED.test(name);
}

function walk(directory, config, found) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const child = join(directory, entry.name);
    if (skipped(child, config)) continue;
    if (entry.isDirectory() && !SKIP_DIRS.has(entry.name)) walk(child, config, found);
    else if (entry.isFile() && measurable(entry.name)) found.push(child);
  }
}

function skipped(path, config) {
  return excluded(path, config.exclude) || config.themeFiles.includes(path);
}

function sourceFiles(paths, config) {
  const found = [];
  for (const path of paths) {
    if (skipped(path, config)) continue;
    if (statSync(path).isDirectory()) walk(path, config, found);
    else if (measurable(path)) found.push(path);
  }
  return found;
}

const REGIONS = [
  { start: '"', end: '"', escapes: true },
  { start: "'", end: "'", escapes: true },
  { start: "`", end: "`", escapes: true },
  { start: "//", end: "\n", escapes: false },
  { start: "/*", end: "*/", escapes: false },
];

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

function blanked(region) {
  return region.replace(/[^\n]/g, " ");
}

function withoutStringsAndComments(text) {
  const out = [];
  let index = 0;
  while (index < text.length) {
    const opened = opener(text, index);
    if (opened === null) {
      out.push(text[index]);
      index++;
      continue;
    }
    const end = regionEnd(text, index, opened);
    out.push(blanked(text.slice(index, end)));
    index = end;
  }
  return out.join("");
}

function balanced(text, open, close) {
  const opening = text[open];
  let depth = 0;
  for (let index = open; index < text.length; index++) {
    if (text[index] === opening) depth++;
    else if (text[index] === close && --depth === 0) return text.slice(open, index);
  }
  return text.slice(open);
}

function lineOf(text, index) {
  return text.slice(0, index).split("\n").length;
}

function effectViolations(file, code) {
  const found = [];
  for (const match of code.matchAll(EFFECT)) {
    const open = match.index + match[0].length - 1;
    if (!DATA_WORK.test(balanced(code, open, ")"))) continue;
    found.push({
      file,
      line: lineOf(code, match.index),
      rule: "effect-data",
      text: "useEffect",
      hint: EFFECT_HINT,
    });
  }
  return found;
}

function inlineTypeViolations(file, code, allow) {
  const found = [];
  for (const match of code.matchAll(STYLE_ATTRIBUTE)) {
    const open = match.index + match[0].length - 1;
    for (const hit of balanced(code, open, "}").matchAll(INLINE_TYPE)) {
      if (allow.includes(hit[0].trim())) continue;
      found.push({
        file,
        line: lineOf(code, open + hit.index),
        rule: "inline-type-declaration",
        text: hit[0].trim(),
        hint: INLINE_TYPE_HINT,
      });
    }
  }
  return found;
}

function matchedText(match) {
  return match.trim().replace(/["']/g, "");
}

function ruleViolations(file, lines, rule, allow) {
  const found = [];
  lines.forEach((line, index) => {
    for (const match of line.matchAll(rule.pattern)) {
      const text = matchedText(match[0]);
      if (allow.includes(text)) continue;
      found.push({ file, line: index + 1, rule: rule.name, text, hint: rule.hint });
    }
  });
  return found;
}

function check(file, config) {
  const text = readFileSync(file, "utf8");
  const lines = text.split("\n");
  const found = RULES.filter((rule) => rule.files.test(file)).flatMap((rule) =>
    ruleViolations(file, lines, rule, config.allow),
  );
  if (!MARKUP.test(file)) return found;
  const code = withoutStringsAndComments(text);
  return [
    ...found,
    ...effectViolations(file, code),
    ...inlineTypeViolations(file, code, config.allow),
  ];
}

function fail(message) {
  process.stderr.write(`error: ${message}\n`);
  process.exit(2);
}

function settings(flags) {
  const file = "package.json";
  const configured = existsSync(file)
    ? (JSON.parse(readFileSync(file, "utf8")).conformance ?? {})
    : {};
  const resolved = { ...DEFAULTS, ...configured, ...flags };
  for (const key of Object.keys(DEFAULTS)) {
    if (!Array.isArray(resolved[key])) fail(`${key} must be an array.`);
  }
  return resolved;
}

const VALUE_FLAGS = new Map([
  ["--exclude", "exclude"],
  ["--allow", "allow"],
  ["--theme-file", "themeFiles"],
]);

function parseArgs(argv) {
  const out = { paths: [], flags: {} };
  for (let index = 0; index < argv.length; index++) {
    const arg = argv[index];
    if (arg === "--help" || arg === "-h") {
      process.stdout.write(`${USAGE}\n`);
      process.exit(0);
    } else if (VALUE_FLAGS.has(arg)) {
      const key = VALUE_FLAGS.get(arg);
      out.flags[key] = [...(out.flags[key] ?? []), argv[++index]];
    } else if (arg.startsWith("-")) fail(`unknown option ${arg}\n\n${USAGE}`);
    else out.paths.push(arg);
  }
  if (out.paths.length === 0) fail(`no paths given\n\n${USAGE}`);
  return out;
}

function count(total, noun) {
  return `${total} ${noun}${total === 1 ? "" : "s"}`;
}

function ordering(one, other) {
  return one.file === other.file ? one.line - other.line : one.file.localeCompare(other.file);
}

function report(violations) {
  const ordered = [...violations].sort(ordering);
  for (const violation of ordered) {
    process.stderr.write(
      `${violation.file}:${violation.line}  ${violation.rule}  ${violation.text}\n`,
    );
  }
  process.stderr.write("\n");
  for (const rule of new Set(ordered.map((violation) => violation.rule))) {
    const { hint } = ordered.find((violation) => violation.rule === rule);
    process.stderr.write(`${rule}: ${hint}\n`);
  }
  process.stderr.write(
    `\nFAIL: ${count(ordered.length, "place")} above step outside the theme or fetch\n` +
      "inside an effect. Both look correct in the code you are reading: a literal\n" +
      "colour renders fine until the theme switches, and an effect that fetches works\n" +
      "until something else needs the same data. Fix the code rather than the check.\n" +
      "A value that genuinely belongs outside the theme goes in conformance.allow in\n" +
      "package.json, where the exception lands in the diff and gets reviewed.\n",
  );
}

function main(argv) {
  const { paths, flags } = parseArgs(argv);
  const config = settings(flags);
  const files = sourceFiles(paths, config);
  if (files.length === 0) fail(`no source files under ${paths.join(", ")}`);
  const violations = files.flatMap((file) => check(file, config));
  if (violations.length > 0) {
    report(violations);
    return 1;
  }
  process.stdout.write(`conformance ok: ${count(files.length, "file")}, ${CHECKS} checks\n`);
  return 0;
}

process.exit(main(process.argv.slice(2)));
