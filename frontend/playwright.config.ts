import { defineConfig, devices } from "@playwright/test";

import { API_PORT, API_URL, DATA_DIR, AUTH_FILE, FEED_PORT, FEED_URL, WEB_PORT, WEB_URL } from "./e2e/env";

/**
 * Browser end-to-end tests. `npm run test:e2e` starts three local servers (fixture feed, API on
 * a throwaway SQLite database, production build of the web app), runs the specs in order and
 * stops the servers. See the README's "Browser tests" section.
 *
 * Projects run one after another because each builds on the data the previous one left:
 *   first-run  →  newsroom (source → headline → research → article → publish → unpublish)  →  layout
 */
const serverEnv = {
  E2E_WEB_PORT: String(WEB_PORT),
  E2E_API_PORT: String(API_PORT),
  E2E_FEED_PORT: String(FEED_PORT),
  E2E_DATA_DIR: DATA_DIR,
};
const serverOutput = process.env.E2E_SERVER_LOGS ? "pipe" : "ignore";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: !!process.env.CI,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  use: {
    ...devices["Desktop Chrome"],
    baseURL: WEB_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "first-run", testMatch: /first-run\.spec\.ts$/ },
    {
      name: "newsroom",
      testMatch: /newsroom-flow\.spec\.ts$/,
      dependencies: ["first-run"],
      use: { storageState: AUTH_FILE },
    },
    {
      name: "layout",
      testMatch: /layout-and-theme\.spec\.ts$/,
      dependencies: ["newsroom"],
      use: { storageState: AUTH_FILE },
    },
  ],
  webServer: [
    {
      command: "node e2e/harness/fixture-feed.mjs",
      url: `${FEED_URL}/robots.txt`,
      env: serverEnv,
      reuseExistingServer: false,
      timeout: 15_000,
      stdout: serverOutput,
      gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 },
    },
    {
      command: "node e2e/harness/api-server.mjs",
      url: `${API_URL}/api/v1/health`,
      env: serverEnv,
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: serverOutput,
      gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 },
    },
    {
      command: "node e2e/harness/web-server.mjs",
      url: `${WEB_URL}/login`,
      env: serverEnv,
      reuseExistingServer: false,
      // Includes `next build`.
      timeout: 600_000,
      stdout: serverOutput,
      gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 },
    },
  ],
});
