import { expect, type Page, test } from "@playwright/test";

const CANARY = "canary-6f1e2d-alice@example.com";
const REQUEST_ID = "0f8c2c1b9d2e4b6f8a1c3e5d7f9b0a2c";

async function nextClientEvent(page: Page): Promise<unknown> {
  const response = await page.waitForResponse(
    (answered) =>
      answered.url().endsWith("/api/client-events") && answered.request().method() === "POST",
  );
  expect(response.status()).toBe(204);
  return response.request().postDataJSON();
}

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

test("an error nothing caught is recorded by its class, never by what it says", async ({
  page,
}) => {
  const said: string[] = [];
  page.on("console", (message) => said.push(message.text()));
  await page.goto("./");
  await expect(page.getByRole("table")).toBeVisible();

  const [thrown] = await Promise.all([
    nextClientEvent(page),
    page.evaluate((canary) => {
      setTimeout(() => {
        throw new TypeError(canary);
      });
    }, CANARY),
  ]);
  const [rejected] = await Promise.all([
    nextClientEvent(page),
    page.evaluate((canary) => {
      void Promise.reject(new RangeError(canary));
    }, CANARY),
  ]);

  expect(thrown).toMatchObject({ events: [{ kind: "uncaught", error: "TypeError", route: "/" }] });
  expect(rejected).toMatchObject({ events: [{ kind: "uncaught", error: "RangeError" }] });
  expect(JSON.stringify([thrown, rejected])).not.toContain(CANARY);
  expect(said.some((line) => line.includes("client event"))).toBe(true);
});

test("an error a boundary caught while rendering is recorded", async ({ page }) => {
  await tasksAnswer(page, 200, { tasks: "not a list" });

  const [posted] = await Promise.all([nextClientEvent(page), page.goto("./")]);

  expect(posted).toMatchObject({ events: [{ kind: "caught", error: "TypeError", route: "/" }] });
});
