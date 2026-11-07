import { defineConfig } from "@playwright/test";

/** The pane spec runs the built UI against the real core (core/tests/demo_core.py) and a fake local model server. */
export default defineConfig({
  testDir: "./ui/e2e",
  timeout: 30_000,
  retries: 0,
  // One core serves every test and holds one conversation per app: parallel tests would interleave their prompts.
  workers: 1,
  fullyParallel: false,
  reporter: process.env["CI"] ? "github" : "list",
  use: {
    baseURL: "http://127.0.0.1:9123",
    headless: true,
  },
  webServer: {
    command: "cd core && PYTHONPATH=../python uv run python tests/demo_core.py --port 9123 --token e2e",
    url: "http://127.0.0.1:9123/health",
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
