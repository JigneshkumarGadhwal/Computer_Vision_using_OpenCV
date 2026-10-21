import { expect, test } from "@playwright/test";

const AUTH_URL = `http://127.0.0.1:${Number(process.env.ZIGGGY_E2E_PORT ?? 8787) + 1}`;

test("token-protected server: login gate, wrong token rejected, stream connects after unlock", async ({ page, request }) => {
  expect((await request.get(`${AUTH_URL}/api/overview`)).status()).toBe(401);
  await page.goto(`${AUTH_URL}/`);
  await page.getByPlaceholder("API token").fill("wrong");
  await page.getByRole("button", { name: "Unlock" }).click();
  await expect(page.getByPlaceholder("API token")).toBeVisible();
  await page.getByPlaceholder("API token").fill("e2e-token");
  await page.getByRole("button", { name: "Unlock" }).click();
  await expect(page.getByText("PAPER · simulated execution")).toBeVisible();
  await expect(page.getByText("◉ stream")).toBeVisible({ timeout: 15_000 });
  await page.reload();
  await expect(page.getByText("◉ stream")).toBeVisible({ timeout: 15_000 });
});
