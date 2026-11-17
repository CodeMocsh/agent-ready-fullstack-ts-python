#!/usr/bin/env node
import { readFileSync } from "node:fs";
import { compositionChecks, SPACING_UTILITY, TYPE_SCALE_STEP } from "./composition.mjs";
import { reportLoose, reportTightened, settled } from "./debt.mjs";
import { assertResolves, count, excluded, fail, ordering, section, sourceFiles } from "./gate.mjs";
import { withProjectRules } from "./project-rules.mjs";
import { balanced, lineOf, withoutStringsAndComments } from "./scan.mjs";

const DEFAULTS = {
  themeFiles: ["src/index.css"],
  allow: [],
  exclude: [],
  primitives: [],
};

const SOURCE = /\.(?:ts|tsx|css)$/;
const GENERATED = /\.(?:d|test|spec)\.(?:ts|tsx)$/;
const MARKUP = /\.tsx?$/;
const STYLESHEET = /\.css$/;

const USAGE = `usage: node devtools/conformance.mjs <paths...> [options]

  --exclude <pattern>   skip a path; repeatable, and trailing /** is optional
  --allow <text>        permit one exact match; repeatable
  --theme-file <path>   a file allowed to define the theme; repeatable
  --primitive <pattern> a path whose code may set its own text, radius and shadow, and use a
                        raw <button> or <input>; repeatable
  --debt <file>         the conformance debt: the findings each file may keep, by rule
  --tighten             lower each count the tree no longer needs, and name each rule
                        that finds nothing
  --record              count the findings of each rule the debt does not name yet

Each finding names its rule and says what to do instead.`;

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

const COLOUR_UTILITIES = [
  "bg|text|border|ring|fill|stroke|shadow|inset-shadow|inset-ring|drop-shadow",
  "from|via|to|outline|decoration|divide|accent|caret|placeholder",
].join("|");

const PALETTE = [
  "slate|gray|grey|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal",
  "cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|white|black",
].join("|");

const UTILITY_EDGE = "(?:-(?:[trblxyse]|offset|shadow))?";

function colourUtility(value) {
  return `(?<![\\w-])(?:${COLOUR_UTILITIES})${UTILITY_EDGE}-${value}`;
}

const COLOUR_PROPERTIES = [
  "color|background|background-color|border-color|outline-color|caret-color|accent-color",
  "text-decoration-color|fill|stroke|backgroundColor|borderColor|outlineColor|caretColor",
  "accentColor|textDecorationColor",
].join("|");

function declarationOf(properties) {
  return `(?<=^|[;{\\s])(?:${properties})\\s*:`;
}

const NAMED_COLOUR = new RegExp(
  `${colourUtility(`\\[(?:${NAMED_COLOURS})\\]`)}` +
    `|(?<=^|[;{\\s"'(])(?:${COLOUR_PROPERTIES})\\s*:\\s*["']?(?:${NAMED_COLOURS})\\b`,
  "g",
);

const PALETTE_UTILITY = new RegExp(colourUtility(`(?:${PALETTE})\\b(?:-\\d{2,3})?`), "g");

const ARBITRARY_SPACING = new RegExp(
  `(?<![\\w-])${SPACING_UTILITY}-\\[(?![^\\]]*--)[^\\]]+\\]`,
  "g",
);

const ALPHA_UTILITIES = COLOUR_UTILITIES.split("|")
  .filter((name) => name !== "text")
  .join("|");

const LITERAL_ALPHA = "\\/(?:\\d{1,3}|\\[(?![^\\]]*--)[^\\]]+\\])";

const LITERAL_LENGTH = "\\[(?![^\\]]*--)-?(?:\\d|\\.\\d)[^\\]]*\\]";

const TOKEN_ALPHA = new RegExp(
  `(?<![\\w-])(?:${ALPHA_UTILITIES}|text(?!-(?:${TYPE_SCALE_STEP})\\/))` +
    `-[a-z][a-z0-9]*(?:-[a-z0-9]+)*${LITERAL_ALPHA}(?![\\w-])`,
  "g",
);

const RAW_STROKE = new RegExp(
  `(?<![\\w-])stroke-(?:\\d+(?:\\.\\d+)?|${LITERAL_LENGTH})(?![\\w-])` +
    `|${declarationOf("stroke-width|stroke-opacity|fill-opacity")}`,
  "g",
);

const MAGIC_PRESENTATION_PROP =
  /(?<![\w-])(?:strokeWidth|strokeOpacity|fillOpacity)\s*=\s*(?:"[^"]*\d[^"]*"|\{[^}]*\d[^}]*\})/g;

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
    pattern: PALETTE_UTILITY,
    hint: "a fixed palette step is not a token; use bg-background, text-muted-foreground, border-border and their siblings",
  },
  {
    name: "named-colour",
    files: SOURCE,
    pattern: NAMED_COLOUR,
    hint: "a colour keyword is a literal like any other and survives the theme switch; use a token",
  },
  {
    name: "token-alpha",
    files: SOURCE,
    pattern: TOKEN_ALPHA,
    hint: "an alpha suffix invents a shade the theme never declared; name the role instead, so a scrim or a hover tint is one token rather than three guesses",
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
    pattern: new RegExp(declarationOf("font-family|font-size|line-height|letter-spacing"), "g"),
    hint: "declare type in the @theme block and reach for it through a utility, so one scale governs the app",
  },
  {
    name: "raw-stroke",
    files: SOURCE,
    pattern: RAW_STROKE,
    hint: "a stroke weight is a theme value like a colour or a size; set --icon-stroke in src/index.css and let the .lucide rule carry it to every icon",
  },
  {
    name: "magic-presentation-prop",
    files: MARKUP,
    pattern: MAGIC_PRESENTATION_PROP,
    hint: "the .lucide rule outranks this prop, so it is already doing nothing; take the weight from --icon-stroke, or name the constant for one-off geometry",
  },
];

const ANNOTATION = "(?:[^=\\n]|=>)*";
const TYPE_PARAMETERS = "(?:<[^(\\n]*>\\s*)?";
const PARAMETERS = "\\([^)]*\\)";
const RETURN_TYPE = "(?::[^=;{\\n]*)?";
const EXPORTED_ARROW = new RegExp(
  `^export\\s+(?:const|let)\\s+\\w+${ANNOTATION}=\\s*(?:async\\s*)?` +
    `(?:function\\b|${TYPE_PARAMETERS}${PARAMETERS}\\s*${RETURN_TYPE}=>|\\w+\\s*=>)`,
  "gm",
);
const EXPORTED_ARROW_HINT =
  "an exported arrow names itself only by assignment and reads as a value rather than a definition; use `export function`";

const EXPORTED_NAME = /^export\s+(?:default\s+)?function\s+([A-Z]\w*)|^export\s*\{([^}]*)\}/gm;
const FILENAME_HINT =
  "the file name is how anything finds a component without reading it; name the file for what it exports, in kebab-case";

const EFFECT = /\buse(?:Layout)?Effect\s*\(/g;
const DATA_WORK = /\bawait\b|\.then\s*\(|\bfetch\s*\(/;
const EFFECT_HINT =
  "data belongs in a TanStack Query hook; an effect that fetches misses the cache, the loading and error states, and the handlers the tests run against";

const MUTATION_START = /\b(?:mutate|mutateAsync)\s*\(/;
const LISTENS = /\baddEventListener\s*\(/;
const EFFECT_MUTATION_HINT =
  "an effect that writes is the client answering its own render; start the mutation from the person's action, or register a listener for that action in the effect";

const SYNTHETIC_INPUT = /\.click\s*\(|\brequestSubmit\s*\(|\bdispatchEvent\s*\(/;
const EFFECT_INPUT_HINT =
  "an effect that clicks, submits or dispatches an event fakes a person's input, which src/query-client.ts counts as an outside cause; do the work the input would start, from a real outside cause";

const INVALIDATION = /\.(?:invalidateQueries|refetchQueries|resetQueries)\s*\(/g;
const INVALIDATOR = /(?:^|\/)src\/query-client\.ts$/;
const INVALIDATION_HINT =
  "a read starts from an outside cause, and a call here can be the client answering its own cache; name the keys in a mutation's meta.invalidates";

const SERVER_EVENT = /\binvalidateForServerEvent\b/g;
const STREAM = /(?:^|\/)src\/(?:streams\/|query-client\.ts$)/;
const SERVER_EVENT_HINT =
  "only a stream hears a server event; put the code that receives the stream under src/streams/, and call invalidateForServerEvent there";

const STYLE_ATTRIBUTE = /\bstyle\s*=\s*\{/g;
const INLINE_TYPE = /(?<![\w-])(?:fontFamily|fontSize|lineHeight|letterSpacing)\s*:/g;
const INLINE_TYPE_HINT =
  "an inline font or size bypasses the theme entirely; use a class from the scale";

function measurable(name) {
  return SOURCE.test(name) && !GENERATED.test(name);
}

function skipped(path, config) {
  return excluded(path, config.exclude) || config.themeFiles.includes(path);
}

function effectsWhoseBody(code, work) {
  return [...code.matchAll(EFFECT)].filter((match) =>
    work.test(balanced(code, match.index + match[0].length - 1, ")")),
  );
}

function called(match) {
  return match[0].replace(/\s*\($/, "");
}

function effectData(_file, code) {
  return effectsWhoseBody(code, DATA_WORK).map((match) => ({
    index: match.index,
    text: "useEffect",
  }));
}

function effectMutations(_file, code) {
  return effectsWhoseBody(code, MUTATION_START)
    .filter((match) => !LISTENS.test(balanced(code, match.index + match[0].length - 1, ")")))
    .map((match) => ({ index: match.index, text: called(match) }));
}

function effectInputs(_file, code) {
  return effectsWhoseBody(code, SYNTHETIC_INPUT).map((match) => ({
    index: match.index,
    text: called(match),
  }));
}

function invalidations(file, code) {
  if (INVALIDATOR.test(file)) return [];
  return [...code.matchAll(INVALIDATION)].map((match) => ({
    index: match.index,
    text: called(match).slice(1),
  }));
}

function serverEvents(file, code) {
  if (STREAM.test(file)) return [];
  return [...code.matchAll(SERVER_EVENT)].map((match) => ({ index: match.index, text: match[0] }));
}

function inlineTypes(_file, code, allow) {
  const found = [];
  for (const match of code.matchAll(STYLE_ATTRIBUTE)) {
    const open = match.index + match[0].length - 1;
    for (const hit of balanced(code, open, "}").matchAll(INLINE_TYPE)) {
      const text = hit[0].trim();
      if (!allow.includes(text)) found.push({ index: open + hit.index, text });
    }
  }
  return found;
}

function expectedExport(file) {
  const stem = file.replace(/^.*[/\\]/, "").replace(/\.tsx$/, "");
  return stem
    .split("-")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join("");
}

function exportedNames(code) {
  const found = [];
  for (const match of code.matchAll(EXPORTED_NAME)) {
    if (match[1] === undefined) {
      for (const listed of match[2].split(",")) {
        const name = listed.split(" as ").pop().trim();
        if (/^[A-Z]\w*$/.test(name)) found.push({ name, index: match.index });
      }
    } else {
      found.push({ name: match[1], index: match.index });
    }
  }
  return found;
}

function filenameExports(file, code) {
  if (!/\.tsx$/.test(file)) return [];
  const exported = exportedNames(code);
  const wanted = expectedExport(file);
  if (exported.length === 0 || exported.some((entry) => entry.name === wanted)) return [];
  return [{ index: exported[0].index, text: exported[0].name }];
}

function exportedArrows(_file, code) {
  return [...code.matchAll(EXPORTED_ARROW)].map((match) => ({
    index: match.index,
    text: match[0].split("=")[0].trim(),
  }));
}

const CODE_CHECKS = [
  { name: "effect-data", hint: EFFECT_HINT, find: effectData },
  { name: "effect-mutation", hint: EFFECT_MUTATION_HINT, find: effectMutations },
  { name: "effect-input", hint: EFFECT_INPUT_HINT, find: effectInputs },
  { name: "query-invalidation", hint: INVALIDATION_HINT, find: invalidations },
  { name: "server-event-outside-a-stream", hint: SERVER_EVENT_HINT, find: serverEvents },
  { name: "inline-type-declaration", hint: INLINE_TYPE_HINT, find: inlineTypes },
  { name: "filename-export", hint: FILENAME_HINT, find: filenameExports },
  { name: "exported-function-expression", hint: EXPORTED_ARROW_HINT, find: exportedArrows },
];

function matchedText(match) {
  return match.trim().replace(/["']/g, "");
}

function ruleViolations(file, lines, rule, allow) {
  const found = [];
  lines.forEach((line, index) => {
    for (const match of line.matchAll(rule.pattern)) {
      if (match[0] === "") {
        fail(
          `rule ${rule.name} matched empty text at ${file}:${index + 1}. A finding with no text\n` +
            "says nothing, and a pattern that matches empty text matches at every position.\n" +
            "Make the pattern match at least one character.",
        );
      }
      const text = matchedText(match[0]);
      if (allow.includes(text)) continue;
      found.push({ file, line: index + 1, rule: rule.name, text, hint: rule.hint });
    }
  });
  return found;
}

function isFinding(found, code) {
  return (
    Number.isInteger(found?.index) &&
    found.index >= 0 &&
    found.index <= code.length &&
    typeof found.text === "string"
  );
}

function codeViolations(file, code, codeCheck, allow) {
  const findings = codeCheck.find(file, code, allow);
  if (!(Array.isArray(findings) && findings.every((found) => isFinding(found, code)))) {
    fail(
      `code check ${codeCheck.name} returned something other than [{ index, text }] for ${file}.\n` +
        "index is a position in the code the check was given, and text is what it found there.",
    );
  }
  return findings.map(({ index, text }) => ({
    file,
    line: lineOf(code, index),
    rule: codeCheck.name,
    text,
    hint: codeCheck.hint,
  }));
}

function check(file, config) {
  const text = readFileSync(file, "utf8");
  const lines = text.split("\n");
  const found = config.rules
    .filter((rule) => rule.files.test(file))
    .flatMap((rule) => ruleViolations(file, lines, rule, config.allow));
  if (!MARKUP.test(file)) return found;
  const code = withoutStringsAndComments(text);
  const composition = excluded(file, config.primitives) ? [] : config.compositionChecks;
  return [
    ...found,
    ...config.codeChecks.flatMap((codeCheck) =>
      codeViolations(file, code, codeCheck, config.allow),
    ),
    ...composition.flatMap((codeCheck) => codeViolations(file, text, codeCheck, config.allow)),
  ];
}

function settings(flags) {
  const resolved = { ...DEFAULTS, ...section("conformance"), ...flags };
  for (const key of Object.keys(DEFAULTS)) {
    if (!Array.isArray(resolved[key])) fail(`${key} must be an array.`);
  }
  assertResolves(resolved.exclude, "conformance.exclude");
  assertResolves(resolved.themeFiles, "conformance.themeFiles");
  assertResolves(resolved.primitives, "conformance.primitives");
  const composition = compositionChecks(resolved.themeFiles);
  return {
    ...resolved,
    compositionChecks: composition,
    ...withProjectRules(RULES, CODE_CHECKS, composition),
  };
}

const VALUE_FLAGS = new Map([
  ["--exclude", "exclude"],
  ["--allow", "allow"],
  ["--theme-file", "themeFiles"],
  ["--primitive", "primitives"],
]);

const SWITCHES = new Map([
  ["--record", "record"],
  ["--tighten", "tighten"],
]);

function flagValue(argv, index, flag) {
  if (index >= argv.length || argv[index].startsWith("-"))
    fail(`${flag} needs a value\n\n${USAGE}`);
  return argv[index];
}

function assertedArgs(out) {
  if (out.paths.length === 0) fail(`no paths given\n\n${USAGE}`);
  if ((out.record || out.tighten) && out.debt === null) {
    fail(`--record and --tighten need --debt\n\n${USAGE}`);
  }
  return out;
}

function parseArgs(argv) {
  const out = { paths: [], flags: {}, debt: null, record: false, tighten: false };
  for (let index = 0; index < argv.length; index++) {
    const arg = argv[index];
    if (arg === "--help" || arg === "-h") {
      process.stdout.write(`${USAGE}\n`);
      process.exit(0);
    } else if (VALUE_FLAGS.has(arg)) {
      const key = VALUE_FLAGS.get(arg);
      out.flags[key] = [...(out.flags[key] ?? []), flagValue(argv, ++index, arg)];
    } else if (arg === "--debt") out.debt = flagValue(argv, ++index, arg);
    else if (SWITCHES.has(arg)) out[SWITCHES.get(arg)] = true;
    else if (arg.startsWith("-")) fail(`unknown option ${arg}\n\n${USAGE}`);
    else out.paths.push(arg);
  }
  return assertedArgs(out);
}

function report(violations, closing) {
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
  process.stderr.write(closing(ordered.length));
}

function outsideTheRules(places, debt) {
  return (
    `\nFAIL: ${count(places, "place")} above step outside the theme, read\n` +
    "outside a query, or start a read or a write with no outside cause. Each looks\n" +
    "correct in the code you are reading: a literal colour renders fine until the\n" +
    "theme switches, an effect that fetches works until something else needs the\n" +
    "same data, and an invalidation works until it answers its own read. Fix the\n" +
    "code rather than the check.\n" +
    "A value that genuinely belongs outside the theme goes in conformance.allow in\n" +
    "package.json, where the exception lands in the diff and gets reviewed.\n" +
    (debt === null
      ? ""
      : `A file may keep no more findings of a rule than ${debt} counts for it.\n` +
        "Fix the code. Do not raise a count.\n")
  );
}

function notRecorded(debt) {
  return (findings) =>
    `\nFAIL: ${count(findings, "finding")} above break a rule that ${debt} does not name yet.\n` +
    "Fix each finding your change made. Then, if an update or a new project rule brought\n" +
    "the rule, run `pnpm conformance:record` once. It counts the findings of those rules\n" +
    "only, and a reviewer sees the counts in the diff.\n";
}

function reportedClean(outcome, path) {
  if (outcome.recorded.length > 0) {
    process.stdout.write(
      `conformance debt recorded for ${count(outcome.recorded.length, "rule")} -> ${path}\n`,
    );
  }
  if (outcome.tightenedFrom.length > 0) reportTightened(outcome.tightenedFrom, path);
  if (outcome.named.length > 0) {
    process.stdout.write(
      `conformance debt names ${count(outcome.named.length, "new rule")} -> ${path}. Commit it.\n`,
    );
  }
  const debt = outcome.known ? path : null;
  if (outcome.over.length > 0) report(outcome.over, (places) => outsideTheRules(places, debt));
  if (outcome.unrecorded.length > 0) report(outcome.unrecorded, notRecorded(path));
  if (outcome.loose.length > 0) reportLoose(outcome.loose, path);
  return outcome.over.length + outcome.unrecorded.length + outcome.loose.length === 0;
}

function main(argv) {
  const { paths, flags, debt, record, tighten } = parseArgs(argv);
  const options = { path: debt, record, tighten };
  const config = settings(flags);
  const files = sourceFiles(paths, {
    matches: measurable,
    skipped: (path) => skipped(path, config),
  });
  if (files.length === 0) fail(`no source files under ${paths.join(", ")}`);
  const violations = files.flatMap((file) => check(file, config));
  const rules = [...config.rules, ...config.codeChecks, ...config.compositionChecks].map(
    (rule) => rule.name,
  );
  const outcome = settled(violations, { ...options, scanned: new Set(files), rules });
  if (!reportedClean(outcome, options.path)) return 1;
  const kept = outcome.owed > 0 ? `, ${count(outcome.owed, "finding")} owed in the debt` : "";
  process.stdout.write(
    `conformance ok: ${count(files.length, "file")}, ${rules.length} checks${kept}\n`,
  );
  return 0;
}

process.exit(main(process.argv.slice(2)));
