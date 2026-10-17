const RELOADED = "missing-chunk-reloaded:";

export type Tab = {
  addEventListener: Window["addEventListener"];
  location: Pick<Location, "reload">;
};

export type Session = Pick<Storage, "getItem" | "setItem">;

export function reloadOnceOnMissingChunk(tab: Tab, session: () => Session, build: string): void {
  tab.addEventListener("vite:preloadError", () => {
    const key = `${RELOADED}${build}`;
    const storage = session();
    if (storage.getItem(key) !== null) {
      return;
    }
    storage.setItem(key, "1");
    tab.location.reload();
  });
}
