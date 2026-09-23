import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/browser",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  maxFailures: 1,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:18766",
    ...devices["Desktop Chrome"],
    channel: process.env.CONTENTFLOW_E2E_BROWSER_CHANNEL || undefined,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    serviceWorkers: "block",
  },
  webServer: [
    { command: "node scripts/e2e-service.mjs api", url: "http://127.0.0.1:18765/health/ready", reuseExistingServer: false, timeout: 120_000 },
    { command: "node scripts/e2e-service.mjs web", url: "http://127.0.0.1:18766", reuseExistingServer: false, timeout: 120_000 },
  ],
});
