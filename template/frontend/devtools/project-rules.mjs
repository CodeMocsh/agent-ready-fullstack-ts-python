import * as project from "./conformance.rules.mjs";
import { fail } from "./gate.mjs";

const RULES_MODULE = "devtools/conformance.rules.mjs";
const RULE_NAME = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;
const PATTERN_RULE_KEYS = "files,hint,name,pattern";
const CODE_CHECK_KEYS = "find,hint,name";

function regExpWith(value, global) {
  return value instanceof RegExp && value.global === global && !value.sticky;
}

function refuse(index, problem) {
  fail(
    `rule ${index + 1} in ${RULES_MODULE} ${problem}.\n` +
      "A pattern rule is { name, files, pattern, hint }, and a code check is\n" +
      "{ name, hint, find }. conformance runs no rule until every rule passes.",
  );
}

function assertPatternRule(rule, index) {
  if (!regExpWith(rule.files, false)) {
    refuse(index, "needs files, a RegExp with neither the g nor the y flag");
  }
  if (!regExpWith(rule.pattern, true)) {
    refuse(index, "needs pattern, a RegExp with the g flag and without the y flag");
  }
}

function assertShape(rule, index) {
  if (typeof rule !== "object" || rule === null) refuse(index, "is not an object");
  const keys = Object.keys(rule).sort().join(",");
  if (keys !== PATTERN_RULE_KEYS && keys !== CODE_CHECK_KEYS) {
    refuse(index, `has the keys ${keys || "(none)"}`);
  }
  if (typeof rule.name !== "string" || !RULE_NAME.test(rule.name)) {
    refuse(index, "needs a name in kebab-case");
  }
  if (typeof rule.hint !== "string" || rule.hint.trim() === "") refuse(index, "needs a hint");
  if (keys === PATTERN_RULE_KEYS) assertPatternRule(rule, index);
  else if (typeof rule.find !== "function") refuse(index, "needs find, a function");
}

export function withProjectRules(templateRules, templateChecks) {
  const rules = project.RULES;
  if (!Array.isArray(rules)) fail(`${RULES_MODULE} must export RULES as an array.`);
  const taken = new Set([...templateRules, ...templateChecks].map((rule) => rule.name));
  rules.forEach((rule, index) => {
    assertShape(rule, index);
    if (taken.has(rule.name)) {
      fail(
        `rule ${index + 1} in ${RULES_MODULE} is named ${rule.name}, and a rule of that name\n` +
          "already exists. The report gives one hint for each name, so one of the two would\n" +
          "point at the other's advice. conformance runs no rule until the project's rule has a\n" +
          "name of its own.",
      );
    }
    taken.add(rule.name);
  });
  return {
    rules: [...templateRules, ...rules.filter((rule) => Object.hasOwn(rule, "pattern"))],
    codeChecks: [...templateChecks, ...rules.filter((rule) => Object.hasOwn(rule, "find"))],
  };
}
