import { spawnSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

export type Run = {
  status: number | null;
  stdout: string;
  stderr: string;
  path: string;
  lines: string[];
};

export const NO_PROJECT_RULES = "export const RULES = [];";

export function gate(script: string, prefix: string) {
  let root = "";
  let directory = "";

  return {
    open() {
      root = mkdtempSync(join(tmpdir(), `${prefix}-`));
      directory = join(root, "tree");
      writeFileSync(join(root, "package.json"), "{}\n");
      cpSync("devtools", join(root, "devtools"), { recursive: true });
      mkdirSync(join(root, "src"));
      writeFileSync(join(root, "src", "index.css"), "");
      mkdirSync(directory);
    },
    at(relative: string) {
      return join(root, relative);
    },
    close() {
      rmSync(root, { recursive: true, force: true });
    },
    run(name: string, source: string[], rulesModule = NO_PROJECT_RULES, args: string[] = []): Run {
      writeFileSync(join(root, "devtools", "conformance.rules.mjs"), `${rulesModule}\n`);
      const path = join(directory, name);
      mkdirSync(dirname(path), { recursive: true });
      writeFileSync(path, `${source.join("\n")}\n`);
      const finished = spawnSync("node", [`devtools/${script}`, directory, ...args], {
        cwd: root,
        encoding: "utf8",
      });
      return {
        status: finished.status,
        stdout: finished.stdout,
        stderr: finished.stderr,
        path,
        lines: finished.stderr.split("\n").filter((line) => line.startsWith(`${path}:`)),
      };
    },
  };
}
