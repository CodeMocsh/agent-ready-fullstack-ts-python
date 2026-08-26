import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export type Run = {
  status: number | null;
  stdout: string;
  path: string;
  lines: string[];
};

export function gate(script: string, prefix: string) {
  let directory = "";

  return {
    open() {
      directory = mkdtempSync(join(tmpdir(), `${prefix}-`));
    },
    close() {
      rmSync(directory, { recursive: true, force: true });
    },
    run(name: string, source: string[]): Run {
      const path = join(directory, name);
      writeFileSync(path, `${source.join("\n")}\n`);
      const finished = spawnSync("node", [`devtools/${script}`, directory], {
        encoding: "utf8",
      });
      return {
        status: finished.status,
        stdout: finished.stdout,
        path,
        lines: finished.stderr.split("\n").filter((line) => line.startsWith(`${path}:`)),
      };
    },
  };
}
