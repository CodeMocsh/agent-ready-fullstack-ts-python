#!/usr/bin/env node
import { readFileSync } from "node:fs";
import { assertResolves, count, excluded, fail, ordering, section, sourceFiles } from "./gate.mjs";
import { lineOf, withoutStringsAndComments } from "./scan.mjs";

const DEFAULTS = {
  themeFiles: ["src/index.css"],
  allow: [],
  exclude: [],
};

const SOURCE = /\.(?:ts|tsx|css)$/;
const GENERATED = /\.(?:d|test|spec)\.(?:ts|tsx)$/;
const MARKUP = /\.tsx?$/;
const STYLESHEET = /\.css$/;

const USAGE = `usage: node devtools/conformance.mjs <paths...> [options]

  --exclude <pattern>   skip a path; repeatable, and trailing /** is optional
  --allow <text>        permit one exact match; repeatable
  --theme-file <path>   a file allowed to define the theme; repeatable

See AGENTS.md for the rules these checks enforce.`;

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

const ARBITRARY_SPACING =
  /(?<![\w-])-?(?:p[xytrbles]?|m[xytrbles]?|gap(?:-[xy])?|space-[xy])-\[(?![^\]]*--)[^\]]+\]/g;

const TYPE_SCALE_STEP = "base|xs|sm|lg|\\d*xl";

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

const EFFECT = /\buseEffect\s*\(/g;
const DATA_WORK = /\bawait\b|\.then\s*\(|\bfetch\s*\(/;
const EFFECT_HINT =
  "data belongs in a TanStack Query hook; an effect that fetches misses the cache, the loading and error states, and the handlers the tests run against";

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

function balanced(text, open, close) {
  const opening = text[open];
  let depth = 0;
  for (let index = open; index < text.length; index++) {
    if (text[index] === opening) depth++;
    else if (text[index] === close && --depth === 0) return text.slice(open, index);
  }
  return text.slice(open);
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

function filenameExportViolations(file, code) {
  if (!/\.tsx$/.test(file)) return [];
  const exported = exportedNames(code);
  const wanted = expectedExport(file);
  if (exported.length === 0 || exported.some((entry) => entry.name === wanted)) return [];
  return [
    {
      file,
      line: lineOf(code, exported[0].index),
      rule: "filename-export",
      text: exported[0].name,
      hint: FILENAME_HINT,
    },
  ];
}

function exportedArrowViolations(file, code) {
  return [...code.matchAll(EXPORTED_ARROW)].map((match) => ({
    file,
    line: lineOf(code, match.index),
    rule: "exported-function-expression",
    text: match[0].split("=")[0].trim(),
    hint: EXPORTED_ARROW_HINT,
  }));
}

const CODE_CHECKS = [
  effectViolations,
  inlineTypeViolations,
  filenameExportViolations,
  exportedArrowViolations,
];

const CHECKS = RULES.length + CODE_CHECKS.length;

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
  return [...found, ...CODE_CHECKS.flatMap((run) => run(file, code, config.allow))];
}

function settings(flags) {
  const resolved = { ...DEFAULTS, ...section("conformance"), ...flags };
  for (const key of Object.keys(DEFAULTS)) {
    if (!Array.isArray(resolved[key])) fail(`${key} must be an array.`);
  }
  assertResolves(resolved.exclude, "conformance.exclude");
  assertResolves(resolved.themeFiles, "conformance.themeFiles");
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
  const files = sourceFiles(paths, {
    matches: measurable,
    skipped: (path) => skipped(path, config),
  });
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
