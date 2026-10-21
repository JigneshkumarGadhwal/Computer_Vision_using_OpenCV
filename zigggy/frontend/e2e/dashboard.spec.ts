import { expect, test } from "@playwright/test";

test("dashboard shows live backend state and controls work", async ({ page, request }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/#overview");
  await expect(page.getByText("PAPER · simulated execution")).toBeVisible();
  // live activity log receives real backend events
  await expect(page.locator("text=DISCOVER").first()).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("Markets scanned")).toBeVisible();

  // Pause trades -> backend state changes -> badge visible
  await page.getByRole("button", { name: "Pause trades" }).click();
  await expect(page.getByText("NEW TRADES PAUSED", { exact: true })).toBeVisible();
  expect((await (await request.get("/api/overview")).json()).trading_paused).toBe(true);
  await page.getByRole("button", { name: "Resume trades" }).click();
  await expect(page.getByText("NEW TRADES PAUSED", { exact: true })).toBeHidden();

  // Emergency stop through the confirmation modal, then reset
  await page.getByRole("button", { name: "■ E-STOP" }).click();
  await page.getByRole("button", { name: "Engage emergency stop" }).click();
  await expect(page.getByText(/^■ EMERGENCY STOP ACTIVE/)).toBeVisible();
  expect((await (await request.get("/api/overview")).json()).emergency_stop).toBe(true);
  await page.getByRole("button", { name: /Reset \(mode → STOPPED\)/ }).click();
  await expect(page.getByText(/^■ EMERGENCY STOP ACTIVE/)).toBeHidden();

  // Settings: switch back to paper; LIVE button opens gated modal with disabled activation
  await page.goto("/#settings");
  await page.getByRole("button", { name: "Paper mode" }).click();
  await expect(page.getByText("PAPER · simulated execution")).toBeVisible();
  await page.getByRole("button", { name: "Live mode…" }).click();
  await expect(page.getByRole("button", { name: "Activate LIVE" })).toBeDisabled();
  await page.getByRole("button", { name: "Close" }).click();

  // Every tab renders without crashing
  for (const tab of ["scanner", "spread", "agents", "portfolio", "backtest", "health"]) {
    await page.goto(`/#${tab}`);
    await page.waitForTimeout(600);
  }
  await page.goto("/#scanner");
  await expect(page.locator("td", { hasText: "[SIM]" }).first()).toBeVisible({ timeout: 20_000 });
  expect(errors).toEqual([]);
});
