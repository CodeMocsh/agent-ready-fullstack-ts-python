import type { Page } from "@playwright/test";
import { CANARY, nextClientEvent, REQUEST_ID } from "./client-events";
import { expect, test } from "./signed-in";

async function tasksAnswer(page: Page, status: number, body: unknown): Promise<void> {
  await page.route("**/api/tasks", (route) =>
    route.fulfill({
      status,
      contentType: "application/json",
      headers: { "x-request-id": REQUEST_ID },
      body: JSON.stringify(body),
    }),
  );
}

test("a query that fails is recorded with its status and the id it answered with", async ({
  page,
}) => {
  await tasksAnswer(page, 500, { detail: CANARY });

  const [posted] = await Promise.all([nextClientEvent(page), page.goto("./")]);

  expect(posted).toEqual({
    events: [{ kind: "query", route: "/", error: "ApiError", status: 500, request_id: REQUEST_ID }],
  });
  expect(JSON.stringify(posted)).not.toContain(CANARY);
});

test("a mutation that fails is recorded", async ({ page }) => {
  await page.goto("./");
  const title = `Client event ${Date.now()}`;
  await page.getByLabel("New task title").fill(title);
  await page.getByRole("button", { name: "Add" }).click();
  await expect(page.getByText(title)).toBeVisible();
  await page.route("**/api/tasks/*", (route) =>
    route.fulfill({ status: 404, contentType: "application/json", body: '{"detail":"gone"}' }),
  );

  const [posted] = await Promise.all([
    nextClientEvent(page),
    page.getByLabel(`Mark "${title}" as done`).click(),
  ]);

  expect(posted).toMatchObject({ events: [{ kind: "mutation", error: "ApiError", status: 404 }] });
  await page.unroute("**/api/tasks/*");
  await page.getByRole("button", { name: `Delete "${title}"` }).click();
  await expect(page.getByText(title)).toHaveCount(0);
});

test("an error a boundary caught while rendering is recorded", async ({ page }) => {
  await tasksAnswer(page, 200, { tasks: "not a list" });

  const [posted] = await Promise.all([nextClientEvent(page), page.goto("./")]);

  expect(posted).toMatchObject({ events: [{ kind: "caught", error: "TypeError", route: "/" }] });
});
