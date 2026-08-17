import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

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

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
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
