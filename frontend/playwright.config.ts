import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests", testMatch: "**/*.spec.ts", fullyParallel: true,
  use: { baseURL: "http://127.0.0.1:3100", headless: true, trace: "retain-on-failure" },
  webServer: { command: "npm run start -- --hostname 127.0.0.1 --port 3100", url: "http://127.0.0.1:3100", reuseExistingServer: false, timeout: 120000 },
});
