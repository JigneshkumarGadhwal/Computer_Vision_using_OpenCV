import { defineConfig } from "@playwright/test";

const PORT = Number(process.env.ZIGGGY_E2E_PORT ?? 8787);
const DATA = process.env.ZIGGGY_E2E_DATA ?? `/tmp/zigggy-e2e-${Date.now()}`;

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
    command: `.venv/bin/zigggy serve`,
    cwd: "..",
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: { ZIGGGY_PROVIDERS: "sim", ZIGGGY_PORT: String(PORT), ZIGGGY_DATA_DIR: DATA, ZIGGGY_SCAN_INTERVAL_S: "5",
           ZIGGGY_LOG_JSON: "false", ZIGGGY_LOG_LEVEL: "WARNING" },
  }, {
    command: `.venv/bin/zigggy serve`,
    cwd: "..",
    url: `http://127.0.0.1:${PORT + 1}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: { ZIGGGY_PROVIDERS: "sim", ZIGGGY_PORT: String(PORT + 1), ZIGGGY_DATA_DIR: `${DATA}-auth`, ZIGGGY_API_TOKEN: "e2e-token",
           ZIGGGY_LOG_JSON: "false", ZIGGGY_LOG_LEVEL: "WARNING" },
  }],
});
