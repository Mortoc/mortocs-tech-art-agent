import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const here = (p: string) => fileURLToPath(new URL(p, import.meta.url));

export default defineConfig({
  resolve: {
    alias: {
      "@houdini-pi/protocol": here("./protocol/src/index.ts"),
    },
  },
  test: {
    include: ["protocol/src/**/*.test.ts", "ui/src/**/*.test.ts"],
    environment: "node",
  },
});
