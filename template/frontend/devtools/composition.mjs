import { readFileSync } from "node:fs";
import { balanced, regions, withoutStringsAndComments } from "./scan.mjs";

export const TYPE_SCALE_STEP = "base|xs|sm|lg|\\d*xl";
export const SPACING_UTILITY =
  "-?(?:p(?:[xytrblse]|bs|be)?|m(?:[xytrblse]|bs|be)?|gap(?:-[xy])?|space-[xy])";

const WEIGHTS = "thin|extralight|light|normal|medium|semibold|bold|extrabold|black";
const DEFAULT_FONTS = "sans|serif|mono";
const STEPS_BESIDE_THE_SCALE = ["px", "auto", "reverse"];

const START = "(?<![^\\s:!])";
const END = "(?![\\w-])";

const TEXT_HINT =
  "a size, colour, weight, family, leading, tracking or case set outside a primitive is a guess at a text role; use a text role a theme file defines with @utility, or a primitive";
const ELEMENT_HINT =
  "a raw <button> or <input> styles itself again at every use; use a primitive from a folder that conformance.primitives names, and write one there if none fits";
const SURFACE_HINT =
  "a radius or a shadow set outside a primitive builds a surface by hand; use the primitive that owns the surface, or a theme utility";
const SPACING_HINT =
  "the theme sets --spacing: initial, so Tailwind generates nothing for this step and drops it without an error; use a step the theme names with --spacing-*";

function themeOf(themeFiles) {
  const text = themeFiles
    .map((file) => readFileSync(file, "utf8"))
    .join("\n")
    .replace(/\/\*[\s\S]*?\*\//g, " ");
  const blocks = [...text.matchAll(/@theme\b[^{]*\{/g)]
    .map((match) => balanced(text, match.index + match[0].length - 1, "}"))
    .join("\n");
  const named = (prefix) => [
    ...new Set(
      [...blocks.matchAll(new RegExp(`--${prefix}-([a-z0-9-]+?)\\s*:`, "g"))]
        .map((match) => match[1])
        .filter((name) => !name.includes("--")),
    ),
  ];
  return {
    colours: named("color"),
    sizes: named("text"),
    fonts: [...named("font"), ...named("font-weight")],
    spacing: named("spacing"),
    resetsSpacing: /--(?:spacing(?:-\*)?|\*)\s*:\s*initial\b/.test(blocks),
    utilities: new Set([...text.matchAll(/@utility\s+([\w-]+)/g)].map((match) => match[1])),
  };
}

function textPattern(theme) {
  const sizes = [TYPE_SCALE_STEP, ...theme.sizes].join("|");
  const fonts = [WEIGHTS, DEFAULT_FONTS, ...theme.fonts].join("|");
  const kinds = [
    `text-(?:${sizes})(?:\\/[\\w.]+)?`,
    `font-(?:${fonts})`,
    "leading-[a-z0-9.]+",
    "tracking-[a-z0-9.]+",
    "uppercase",
  ];
  if (theme.colours.length > 0) kinds.push(`text-(?:${theme.colours.join("|")})`);
  return new RegExp(`${START}(?:${kinds.join("|")})${END}`, "g");
}

const SURFACE = new RegExp(
  `${START}(?:rounded|shadow)(?:-(?:\\[[^\\]]*\\]|\\([^)]*\\)|[a-z0-9./-]+))?${END}`,
  "g",
);

const SPACING = new RegExp(`${START}${SPACING_UTILITY}-([a-z0-9.]+)${END}`, "g");

const RAW_ELEMENT = /<(?:button|input)(?![\w-])/g;

function classStrings(text, offset = 0) {
  const found = [];
  for (const region of regions(text)) {
    if (region.comment) continue;
    let body = text.slice(region.index + 1, region.end - 1);
    for (const hole of region.holes) {
      const at = hole.start - region.index - 1;
      const width = hole.end - hole.start;
      body = body.slice(0, at) + " ".repeat(width) + body.slice(at + width);
      found.push(...classStrings(text.slice(hole.start, hole.end), offset + hole.start));
    }
    found.push({ index: offset + region.index + 1, text: body });
  }
  return found;
}

function findIn(text, pattern, keep) {
  const found = [];
  for (const string of classStrings(text)) {
    for (const match of string.text.matchAll(pattern)) {
      if (keep(match[0], match)) found.push({ index: string.index + match.index, text: match[0] });
    }
  }
  return found;
}

export function compositionChecks(themeFiles) {
  const theme = themeOf(themeFiles);
  const text = textPattern(theme);
  const exempt = (allow) => (name) => theme.utilities.has(name) || allow.includes(name);
  const steps = new Set([...theme.spacing, ...STEPS_BESIDE_THE_SCALE]);
  return [
    {
      name: "text-outside-a-role",
      hint: TEXT_HINT,
      find: (_file, source, allow) => findIn(source, text, (name) => !exempt(allow)(name)),
    },
    {
      name: "element-outside-a-primitive",
      hint: ELEMENT_HINT,
      find: (_file, source, allow) =>
        [...withoutStringsAndComments(source).matchAll(RAW_ELEMENT)]
          .filter((match) => !allow.includes(match[0]))
          .map((match) => ({ index: match.index, text: match[0] })),
    },
    {
      name: "surface-outside-a-primitive",
      hint: SURFACE_HINT,
      find: (_file, source, allow) => findIn(source, SURFACE, (name) => !exempt(allow)(name)),
    },
    {
      name: "spacing-off-the-scale",
      hint: SPACING_HINT,
      find: (_file, source, allow) =>
        theme.resetsSpacing
          ? findIn(source, SPACING, (name, match) => !(steps.has(match[1]) || exempt(allow)(name)))
          : [],
    },
  ];
}
