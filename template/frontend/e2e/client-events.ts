import { expect, type Page } from "@playwright/test";

export const CANARY = "canary-6f1e2d-alice@example.com";
export const REQUEST_ID = "0f8c2c1b9d2e4b6f8a1c3e5d7f9b0a2c";

export async function nextClientEvent(page: Page): Promise<unknown> {
  const response = await page.waitForResponse(
    (answered) =>
      answered.url().endsWith("/api/client-events") && answered.request().method() === "POST",
  );
  expect(response.status()).toBe(204);
  return response.request().postDataJSON();
}
