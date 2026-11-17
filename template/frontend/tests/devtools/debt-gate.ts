import { readFileSync } from "node:fs";
import { gate, NO_PROJECT_RULES } from "./harness";

export const DEBT = ["--debt", "debt.json"];

export const TWO_COLOURS = [
  "export function Swatch() {",
  "  return <p className='border-[#fff] bg-[#000]'>two</p>;",
  "}",
];

export const ONE_COLOUR = [
  "export function Swatch() {",
  "  return <p className='border-[#fff]'>one</p>;",
  "}",
];

export const CLEAN = [
  "export function Swatch() {",
  "  return <p className='border-border'>none</p>;",
  "}",
];

export const OTHER = ["export function Other() {", "  return null;", "}"];

export const FAINT_RULE =
  "export const RULES = [{ name: 'hairline-as-text', files: /\\.tsx$/, " +
  "pattern: /text-faint/g, hint: 'faint is a hairline colour' }];";

export const FAINT = [
  "export function Swatch() {",
  "  return <p className='text-faint'>faint</p>;",
  "}",
];

export function debtGate() {
  const conformance = gate("conformance.mjs", "debt");
  const debtFile = () => conformance.at("debt.json");
  return {
    conformance,
    debtFile,
    debt: () => JSON.parse(readFileSync(debtFile(), "utf8")),
    run: (source: string[], args: string[] = [], rulesModule = NO_PROJECT_RULES) =>
      conformance.run("swatch.tsx", source, rulesModule, [...DEBT, ...args]),
  };
}

export function rulesOf(lines: string[]) {
  return lines.map((line) => line.split(/\s{2,}/)[1]);
}
