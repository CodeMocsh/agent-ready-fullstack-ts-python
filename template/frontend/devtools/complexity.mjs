#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { excluded, fail, section, sourceFiles } from "./gate.mjs";

const DEFAULTS = {
  cap: 15,
  tolerance: 0.02,
  ceilingFactor: 1.25,
};

const METRIC = "complexity-beyond-first-branch-per-kloc";

const FLOOR = 1;

const RULE = "lint/complexity/noExcessiveCognitiveComplexity";
const SCORE = /Excessive complexity of (\d+) detected/;
const SOURCE = /\.(?:ts|tsx|mts|cts|mjs|js)$/;
const GENERATED = /\.(?:d|test|spec)\.(?:ts|tsx|mts|cts|mjs|js)$/;

const USAGE = `usage: node devtools/complexity.mjs <paths...> [options]

  --baseline <file>     baseline to compare against
  --update-baseline     record the current level instead of checking
  --tighten-baseline    lower a baseline left above the tree, instead of failing on it
  --cap <n>             per-function contribution ceiling for the metric
  --tolerance <n>       allowed relative rise, as a fraction
  --ceiling-factor <n>  multiple of the origin baseline that is never exceeded
  --exclude <pattern>   skip a path; repeatable, and trailing /** is optional

See docs/agent-tooling.md for what each threshold does and where it came from.`;

function measurable(name) {
  return SOURCE.test(name) && !GENERATED.test(name);
}

function nonBlankLines(files) {
  let total = 0;
  for (const file of files) {
    for (const line of readFileSync(file, "utf8").split("\n")) {
      if (line.trim() !== "") total++;
    }
  }
  return total;
}

function measurementConfig() {
  const dir = mkdtempSync(join(tmpdir(), "complexity-"));
  const path = join(dir, "biome.json");
  writeFileSync(
    path,
    JSON.stringify({
      files: {
        includes: [
          "**",
          "!**/*.d.ts",
          "!**/*.test.ts",
          "!**/*.test.tsx",
          "!**/*.spec.ts",
          "!**/*.spec.tsx",
        ],
      },
      formatter: { enabled: false },
      assist: { enabled: false },
      linter: {
        enabled: true,
        rules: {
          recommended: false,
          complexity: {
            noExcessiveCognitiveComplexity: {
              level: "error",
              options: { maxAllowedComplexity: FLOOR },
            },
          },
        },
      },
    }),
  );
  return path;
}

function runBiome(paths, config) {
  const args = ["lint", "--config-path", config, "--reporter=json", "--max-diagnostics=none"];
  try {
    return execFileSync("biome", [...args, ...paths], {
      encoding: "utf8",
      maxBuffer: 256 * 1024 * 1024,
      stdio: ["ignore", "pipe", "ignore"],
    });
  } catch (error) {
    if (error.code === "ENOENT") fail("biome was not found on PATH; run this through pnpm.");
    if (!error.stdout) fail(`biome produced no output (exit ${error.status}).`);
    return error.stdout;
  }
}

function scores(paths, cap) {
  const parsed = JSON.parse(runBiome(paths, measurementConfig()));
  let matched = 0;
  let offered = 0;
  let sum = 0;
  for (const diagnostic of parsed.diagnostics) {
    if (diagnostic.category !== RULE) continue;
    offered++;
    const found = SCORE.exec(diagnostic.message ?? "");
    if (!found) continue;
    matched++;
    sum += Math.min(Number(found[1]), cap) - FLOOR;
  }
  if (offered !== matched) {
    fail("biome's complexity message format changed; this parser needs updating.");
  }
  return { functions: matched, sum };
}

function measure(paths, config) {
  const files = sourceFiles(paths, {
    matches: measurable,
    skipped: (path) => excluded(path, config.exclude),
  });
  if (files.length === 0) fail(`no source files under ${paths.join(", ")}`);
  const lines = nonBlankLines(files);
  const { functions, sum } = scores(files, config.cap);
  return {
    files: files.length,
    lines,
    functions,
    sum,
    density: Number(((sum / lines) * 1000).toFixed(3)),
  };
}

function settings(flags) {
  const resolved = { exclude: [], ...DEFAULTS, ...section("complexity"), ...flags };
  for (const [key, value] of Object.entries(DEFAULTS)) {
    if (typeof resolved[key] !== "number" || !Number.isFinite(resolved[key])) {
      fail(`${key} must be a number (default ${value}).`);
    }
  }
  if (!Array.isArray(resolved.exclude)) fail("exclude must be an array of path patterns.");
  return resolved;
}

const FLAG_NAMES = new Map([
  ["--cap", "cap"],
  ["--tolerance", "tolerance"],
  ["--ceiling-factor", "ceilingFactor"],
]);

const VALUE_FLAGS = new Set(["--baseline", "--exclude", ...FLAG_NAMES.keys()]);

function applyValueFlag(arg, value, out) {
  if (arg === "--baseline") out.baseline = value;
  else if (arg === "--exclude") out.exclude.push(value);
  else out.flags[FLAG_NAMES.get(arg)] = Number(value);
}

function parseArgs(argv) {
  const out = { paths: [], flags: {}, exclude: [], baseline: null, update: false, tighten: false };
  for (let index = 0; index < argv.length; index++) {
    const arg = argv[index];
    if (arg === "--help" || arg === "-h") {
      process.stdout.write(`${USAGE}\n`);
      process.exit(0);
    } else if (arg === "--update-baseline") out.update = true;
    else if (arg === "--tighten-baseline") out.tighten = true;
    else if (VALUE_FLAGS.has(arg)) applyValueFlag(arg, argv[++index], out);
    else if (arg.startsWith("-")) fail(`unknown option ${arg}\n\n${USAGE}`);
    else out.paths.push(arg);
  }
  if (out.exclude.length > 0) out.flags.exclude = out.exclude;
  if (out.paths.length === 0) fail(`no paths given\n\n${USAGE}`);
  return out;
}

function recordBaseline(path, now, previous) {
  const origin = previous?.origin ?? now.density;
  writeFileSync(path, `${JSON.stringify({ metric: METRIC, ...now, origin }, null, 2)}\n`);
  return origin;
}

function writeBaseline(path, now, previous) {
  const origin = recordBaseline(path, now, previous);
  process.stdout.write(`baseline written -> ${path} (origin ${origin})\n`);
}

function assertSameMetric(baseline, previous) {
  if (previous.metric === METRIC) return;
  process.stderr.write(
    `\nFAIL: ${baseline} was recorded under metric "${previous.metric ?? "unnamed"}",\n` +
      `but this script now computes "${METRIC}". Comparing them would measure\n` +
      "nothing. Re-record it with `pnpm complexity:baseline`, which lands in the\n" +
      "diff so the change of instrument is reviewed rather than assumed.\n",
  );
  process.exit(1);
}

function checkCeiling(now, previous, ceilingFactor) {
  const origin = previous.origin ?? previous.density;
  if (!(origin > 0)) return 0;
  const ceiling = origin * ceilingFactor;
  if (now.density <= ceiling) return 0;
  process.stderr.write(
    `\nFAIL: density ${now.density} is above the ceiling of ${ceiling.toFixed(3)} ` +
      `(${ceilingFactor}x the origin baseline of ${origin}).\n` +
      "Accepted drift has accumulated past the absolute limit; this needs\n" +
      "refactoring rather than another baseline update.\n",
  );
  return 1;
}

function reportRise(now, previous, drift, tolerance) {
  process.stderr.write(
    `\nFAIL: density drifted ${previous.density} -> ${now.density} ` +
      `(${percent(drift)}, tolerance ${percent(tolerance)}).\n` +
      "Functions are fattening below the per-function gate; split the ones\n" +
      "that grew. If the rise is genuinely warranted, record it with\n" +
      "--update-baseline — that lands in the diff for review, so it is not\n" +
      "the way to make a build green.\n",
  );
  return 1;
}

function reportSlack(now, previous, drift, tolerance) {
  const admits = (previous.density * (1 + tolerance)) / now.density - 1;
  process.stderr.write(
    `\nFAIL: the baseline is ${magnitude(drift)} above the tree ` +
      `(${previous.density} -> ${now.density}, tolerance ${percent(tolerance)}).\n` +
      "A baseline left this far above the tree has stopped gating: the gap is\n" +
      "slack, and slack is added to whatever the next commit may spend. From here\n" +
      `a single change could raise density by ${percent(admits)} and still pass.\n` +
      "Nobody has to approve the code getting better, but it does have to be\n" +
      "recorded: run `pnpm lint`.\n",
  );
  return 1;
}

function tightenBaseline(baseline, now, previous, drift) {
  recordBaseline(baseline, now, previous);
  process.stdout.write(
    `baseline tightened: density ${previous.density} -> ${now.density} ` +
      `(${percent(drift)}); commit ${baseline}\n`,
  );
  return 0;
}

function driftFrom(now, previous) {
  return (now.density - previous.density) / previous.density;
}

function checkDrift(now, previous, tolerance, baseline, tighten) {
  if (!(previous.density > 0)) return 0;
  const drift = driftFrom(now, previous);
  if (drift > tolerance) return reportRise(now, previous, drift, tolerance);
  if (drift >= -tolerance) {
    process.stdout.write(
      `drift ok: density ${previous.density} -> ${now.density} (${percent(drift)})\n`,
    );
    return 0;
  }
  return tighten
    ? tightenBaseline(baseline, now, previous, drift)
    : reportSlack(now, previous, drift, tolerance);
}

function percent(fraction) {
  return `${fraction >= 0 ? "+" : ""}${(fraction * 100).toFixed(2)}%`;
}

function magnitude(fraction) {
  return `${(Math.abs(fraction) * 100).toFixed(2)}%`;
}

function report(now) {
  process.stdout.write(
    `functions ${now.functions}   source lines ${now.lines}   density ${now.density}\n`,
  );
}

function singleFunctionSwing(now, cap) {
  return ((cap - FLOOR) / now.lines) * 1000;
}

function tooSmallToGate(now, config) {
  return singleFunctionSwing(now, config.cap) > config.tolerance * now.density;
}

function zeroDensity(baseline, now, previous, tighten) {
  process.stdout.write(
    "nothing to ratchet: no function branches beyond a single condition, so this\n" +
      "metric is zero by definition. The per-function gates carry this alone.\n",
  );
  const stale = tighten && baseline && previous && previous.density > 0;
  return stale ? tightenBaseline(baseline, now, previous, driftFrom(now, previous)) : 0;
}

function assertUsableBaseline(baseline, previous) {
  if (previous.density > 0) return;
  process.stderr.write(
    `\nFAIL: ${baseline} records a density of ${previous.density}, so there is no\n` +
      "level to measure a relative rise against. It was recorded while nothing in\n" +
      "the codebase branched beyond a single condition; that is no longer true.\n" +
      "Record the current level with `pnpm complexity:baseline`.\n",
  );
  process.exit(1);
}

function main(argv) {
  const { paths, flags, baseline, update, tighten } = parseArgs(argv);
  const config = settings(flags);
  const now = measure(paths, config);
  report(now);

  const previous =
    baseline && existsSync(baseline) ? JSON.parse(readFileSync(baseline, "utf8")) : null;

  if (update) {
    if (!baseline) fail("--update-baseline needs --baseline <file>");
    writeBaseline(baseline, now, previous);
    return 0;
  }

  if (previous) assertSameMetric(baseline, previous);

  if (!(now.density > 0)) return zeroDensity(baseline, now, previous, tighten);

  if (tooSmallToGate(now, config)) {
    const swing = singleFunctionSwing(now, config.cap).toFixed(2);
    const allowed = (config.tolerance * now.density).toFixed(2);
    process.stdout.write(
      `too small to gate: one function at the cap would move density by ${swing},\n` +
        `and the tolerance only allows ${allowed}. The per-function gates carry this alone.\n`,
    );
    return 0;
  }

  if (!baseline) return 0;
  if (!previous) {
    process.stderr.write(
      `\nFAIL: no baseline at ${baseline}, and this codebase is now large enough\n` +
        "for the codebase-wide checks to mean something. Record the current level:\n" +
        "  pnpm complexity:baseline\n",
    );
    return 1;
  }
  assertUsableBaseline(baseline, previous);

  return (
    checkDrift(now, previous, config.tolerance, baseline, tighten) |
    checkCeiling(now, previous, config.ceilingFactor)
  );
}

process.exit(main(process.argv.slice(2)));
