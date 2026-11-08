#!/usr/bin/env node
import { readFileSync, writeFileSync } from "node:fs";
import { fail } from "./gate.mjs";

const USAGE = `usage: node devtools/parts.mjs <openapi.json> <out.ts>

Writes the path of each GET operation that carries "x-part": true, sorted, as PARTS, and the
maxItems of the part parameter of GET /reads as PATHS_AT_MOST.`;

const [spec, out, ...rest] = process.argv.slice(2);
if (spec === undefined || out === undefined || rest.length > 0) fail(USAGE);

const { paths } = JSON.parse(readFileSync(spec, "utf8"));
if (typeof paths !== "object" || paths === null) fail(`${spec} has no paths`);

const parts = Object.entries(paths)
  .filter(([, operations]) => operations.get?.["x-part"] === true)
  .map(([path]) => path)
  .sort();

const named = paths["/reads"]?.get?.parameters?.find((parameter) => parameter.name === "part");
const most = named?.schema?.maxItems;
if (!Number.isInteger(most)) fail(`${spec} gives GET /reads no maxItems for its part parameter`);

writeFileSync(
  out,
  `export const PARTS: readonly string[] = ${JSON.stringify(parts)};\n` +
    `export const PATHS_AT_MOST = ${most};\n`,
);
