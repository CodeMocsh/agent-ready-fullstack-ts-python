import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The backend half serves bare paths (/tasks). The /api prefix is deployment
// topology rather than contract, so it is added by the client and stripped here;
// a reverse proxy in production does the same thing. Going through a proxy rather
// than enabling CORS keeps the browser on one origin, which is why the backend
// ships no CORS configuration at all.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
