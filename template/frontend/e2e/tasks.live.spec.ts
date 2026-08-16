import { expect, test } from "@playwright/test";

// The mock-mode spec is the one Playwright runs by default. This one needs both
// halves up -- `make dev` in another terminal -- and is how you look at the real
// thing in a browser:
//     pnpm exec playwright test e2e/tasks.live.spec.ts --config playwright.live.config.ts
test("adds a task against the backend half", async ({ page }) => {
  await page.goto("./");

  await expect(page.getByRole("heading", { name: "Tasks" })).toBeVisible();
  await expect(page.getByText("Read AGENTS.md")).toBeVisible();

  const title = `Live task ${Date.now()}`;
  await page.getByLabel("New task title").fill(title);
  await page.getByRole("button", { name: "Add" }).click();

  await expect(page.getByText(title)).toBeVisible();

  await page.reload();
  await expect(page.getByText(title)).toBeVisible();
});
