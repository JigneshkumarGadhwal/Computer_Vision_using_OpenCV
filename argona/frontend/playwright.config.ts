import { defineConfig } from "@playwright/test";

const PORT = Number(process.env.ARGONA_E2E_PORT ?? 8787);
const DATA = process.env.ARGONA_E2E_DATA ?? `/tmp/argona-e2e-${Date.now()}`;

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: 0,
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_PATH ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH } : {},
  },
  // Boots real backends on the SIM venue (simulated data) with isolated databases:
  // one open on loopback (no token) and one requiring a bearer token.
  webServer: [{
    command: `.venv/bin/argona serve`,
    cwd: "..",
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: { ARGONA_PROVIDERS: "sim", ARGONA_PORT: String(PORT), ARGONA_DATA_DIR: DATA, ARGONA_SCAN_INTERVAL_S: "5",
           ARGONA_LOG_JSON: "false", ARGONA_LOG_LEVEL: "WARNING" },
  }, {
    command: `.venv/bin/argona serve`,
    cwd: "..",
    url: `http://127.0.0.1:${PORT + 1}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: { ARGONA_PROVIDERS: "sim", ARGONA_PORT: String(PORT + 1), ARGONA_DATA_DIR: `${DATA}-auth`, ARGONA_API_TOKEN: "e2e-token",
           ARGONA_LOG_JSON: "false", ARGONA_LOG_LEVEL: "WARNING" },
  }],
});
