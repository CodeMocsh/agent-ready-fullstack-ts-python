import { focusManager } from "@tanstack/react-query";
import type { AnyRouter } from "@tanstack/react-router";
import type { RecordClientEvent } from "@/client-events";
import type { Session, Tab } from "@/missing-chunk";

export const BUILD_FILE = "build.json";

const RELOADING_INTO = "build-reloading-into";

export class BuildUnknown extends Error {
  override readonly name = "BuildUnknown";
}

export class BuildNotLoaded extends Error {
  override readonly name = "BuildNotLoaded";
}

export type Build = {
  running: string;
  file: URL;
};

export type Following = {
  router: AnyRouter;
  tab: Pick<Tab, "location">;
  session: () => Session;
  record: RecordClientEvent;
  build: Build;
};

function entryOf(body: unknown): string {
  if (typeof body === "object" && body !== null && "entry" in body) {
    const { entry } = body as { entry: unknown };
    if (typeof entry === "string" && entry !== "") {
      return entry;
    }
  }
  throw new BuildUnknown(`${BUILD_FILE} names no entry`);
}

async function bodyOf(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch (failure: unknown) {
    throw new BuildUnknown(`${BUILD_FILE} is not JSON`, { cause: failure });
  }
}

async function deployedEntry(file: URL): Promise<string> {
  const response = await fetch(file, { cache: "no-cache" });
  if (!response.ok) {
    throw new BuildUnknown(`${BUILD_FILE} answered ${response.status}`);
  }
  return new URL(entryOf(await bodyOf(response)), file).href;
}

export function followTheDeployedBuild({
  router,
  tab,
  session,
  record,
  build,
}: Following): () => void {
  let next: string | null = null;
  let reportedNotLoaded = false;
  const reloadedInto = session().getItem(RELOADING_INTO);
  const notLoaded = reloadedInto === build.running ? null : reloadedInto;
  const learned = (deployed: string) => {
    if (deployed === build.running) {
      next = null;
      return;
    }
    if (deployed !== notLoaded) {
      next = deployed;
      return;
    }
    if (!reportedNotLoaded) {
      reportedNotLoaded = true;
      record(
        "build",
        new BuildNotLoaded(
          `this tab reloaded into ${deployed} and still runs ${build.running}: the host serves an old index.html`,
        ),
      );
    }
  };
  const check = () => {
    deployedEntry(build.file)
      .then(learned)
      .catch((failure: unknown) => record("build", failure));
  };
  const stops = [
    router.subscribe("onBeforeLoad", ({ fromLocation, pathChanged }) => {
      if (fromLocation === undefined || !pathChanged) {
        return;
      }
      if (next !== null) {
        session().setItem(RELOADING_INTO, next);
        tab.location.reload();
        return;
      }
      check();
    }),
    focusManager.subscribe((focused) => {
      if (focused) {
        check();
      }
    }),
  ];
  return () => {
    for (const stop of stops) {
      stop();
    }
  };
}
