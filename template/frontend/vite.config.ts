import { rmSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { type Plugin, type ResolvedConfig, defineConfig } from "vite";

// The backend half serves bare paths (/tasks). The /api prefix is deployment
// topology rather than contract, so it is added by the client and stripped here;
// a reverse proxy in production does the same thing. Going through a proxy rather
// than enabling CORS keeps the browser on one origin, which is why the backend
// ships no CORS configuration at all.
//
// Both ports come from the environment so that two checkouts of this project can
// run at once. They are fixed by default because a stable URL is worth more than
// an ephemeral one for the thing you keep open in a browser -- but a second
// worktree is a normal way to work, and hard-coding meant the second `make dev`
// failed on a port the first one was using.
const FRONTEND_PORT = Number(process.env.FRONTEND_PORT ?? 5173);
const BACKEND_PORT = Number(process.env.BACKEND_PORT ?? 8000);

// Most of this bundle is dependency code every route needs, so with one chunk a
// one-line change to a component re-downloads react-dom for everybody who already
// had it. These groups are split by how often they change rather than by size:
// react moves on its own release cycle, the data and UI libraries on theirs, and
// app code on every deploy. A returning visitor then pays only for what moved.
const VENDOR_GROUPS = [
  { name: "react", test: /node_modules\/(react|react-dom|scheduler)\// },
  { name: "tanstack", test: /node_modules\/@tanstack\// },
  { name: "ui", test: /node_modules\/(@base-ui|@floating-ui|lucide-react)\// },
  { name: "style", test: /node_modules\/(tailwind-merge|clsx|class-variance-authority)\// },
];

// Mock mode leaves two things behind, and only one of them is code. The dynamic import in
// main.tsx is unreachable once VITE_ENABLE_MSW folds to undefined, so rolldown never writes
// the msw chunk -- that one takes care of itself. The worker script does not: it lives in
// public/, which is copied verbatim into every build whatever the mode, so a production
// deploy carries a registerable service worker at its own origin and nothing about the mode
// made it go away. It is inert there, because the worker passes every request through until
// a page sends it MOCK_ACTIVATE and no production page does. Inert is not the same as
// explicable, and "why is there a mock service worker on our domain" is a question this
// template should answer rather than hand to the person who deploys it.
//
// It has to be removed after the fact rather than never copied: public/ is copied outside the
// bundle graph, so generateBundle cannot see the file, and copyPublicDir: false would take
// favicon.svg with it. The worker cannot move out of public/ either -- msw's postinstall
// writes it there, addressed by the msw.workerDirectory key in package.json.
//
// The env is read from the resolved config rather than from process.env, because the value
// comes from whichever .env.[mode] vite loaded, and that is the same source main.tsx reads.
function stripMockWorker(): Plugin {
  let config: ResolvedConfig;
  return {
    name: "strip-mock-worker",
    apply: "build",
    configResolved(resolved) {
      config = resolved;
    },
    closeBundle() {
      if (config.env.VITE_ENABLE_MSW === "true") {
        return;
      }
      rmSync(resolve(config.root, config.build.outDir, "mockServiceWorker.js"), { force: true });
    },
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), stripMockWorker()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: { groups: VENDOR_GROUPS },
      },
    },
  },
  server: {
    port: FRONTEND_PORT,
    strictPort: true,
    proxy: {
      "/api": {
        target: `http://localhost:${BACKEND_PORT}`,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
