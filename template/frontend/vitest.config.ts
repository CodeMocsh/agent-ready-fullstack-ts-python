import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const node = ["tests/devtools/**/*.test.ts"];
const smoke = ["tests/api/**/*.test.ts", "tests/components/**/*.test.tsx", "tests/first-draw.test.tsx"];

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    isolate: false,
    testTimeout: 20000,
    env: {
      VITE_ENABLE_MSW: process.env.CONTRACT_TARGET === "live" ? "false" : "true",
    },
    projects: [
      {
        extends: true,
        test: { name: "node", environment: "node", include: node },
      },
      {
        extends: true,
        test: {
          name: "smoke",
          environment: "jsdom",
          setupFiles: ["./tests/setup.ts"],
          include: smoke,
        },
      },
      {
        extends: true,
        test: {
          name: "dom",
          environment: "jsdom",
          setupFiles: ["./tests/setup.ts"],
          include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
          exclude: [...node, ...smoke],
        },
      },
    ],
  },
});
