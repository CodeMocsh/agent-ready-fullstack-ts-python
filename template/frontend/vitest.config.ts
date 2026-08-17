import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
    // Vitest loads no .env file, so components reading this flag would see it unset
    // and claim the wrong half is answering. Mirror what tests/setup.ts decides from
    // the same variable: the contract run against the backend is the one live case.
    env: {
      VITE_ENABLE_MSW: process.env.CONTRACT_TARGET === "live" ? "false" : "true",
    },
  },
});
