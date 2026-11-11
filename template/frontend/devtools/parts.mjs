#!/usr/bin/env node
import { readFileSync, writeFileSync } from "node:fs";
import { fail } from "./gate.mjs";

const USAGE = `usage: node devtools/parts.mjs <openapi.json> <out.ts>

Writes the path of each GET operation that carries "x-part": true, sorted, as PARTS, the
maxItems of the part parameter of GET /reads as PATHS_AT_MOST, and the path of each GET
operation that answers a page, one with a next, sorted, as PAGES.`;

const [spec, out, ...rest] = process.argv.slice(2);
if (spec === undefined || out === undefined || rest.length > 0) fail(USAGE);

const { paths, components } = JSON.parse(readFileSync(spec, "utf8"));
if (typeof paths !== "object" || paths === null) fail(`${spec} has no paths`);

const parts = Object.entries(paths)
  .filter(([, operations]) => operations.get?.["x-part"] === true)
  .map(([path]) => path)
  .sort();

const schemas = components?.schemas;
const answered = (operation) => {
  const schema = operation?.responses?.["200"]?.content?.["application/json"]?.schema;
  const name = schema?.$ref?.split("/").at(-1);
  return name === undefined ? schema : schemas[name];
};
const pages = Object.entries(paths)
  .filter(([, operations]) => answered(operations.get)?.properties?.next !== undefined)
  .map(([path]) => path)
  .sort();

const named = paths["/reads"]?.get?.parameters?.find((parameter) => parameter.name === "part");
const most = named?.schema?.maxItems;
if (!Number.isInteger(most)) fail(`${spec} gives GET /reads no maxItems for its part parameter`);

writeFileSync(
  out,
  `export const PARTS: readonly string[] = ${JSON.stringify(parts)};\n` +
    `export const PATHS_AT_MOST = ${most};\n` +
    `export const PAGES: readonly string[] = ${JSON.stringify(pages)};\n`,
);
