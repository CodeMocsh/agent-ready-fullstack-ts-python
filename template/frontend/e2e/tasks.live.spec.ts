import { expect, test } from "@playwright/test";

test("adds a task against the backend half", async ({ page }) => {
  await page.goto("./");

  await expect(page.getByRole("heading", { name: "Tasks" })).toBeVisible();
  await expect(page.getByText(/Live mode/)).toBeVisible();
  await expect(page.getByRole("table")).toBeVisible();

  const title = `Live task ${Date.now()}`;
  await page.getByLabel("New task title").fill(title);
  await page.getByRole("button", { name: "Add" }).click();

  await expect(page.getByText(title)).toBeVisible();

  await page.reload();
  await expect(page.getByText(title)).toBeVisible();

  await page.getByRole("button", { name: `Delete "${title}"` }).click();
  await expect(page.getByText(title)).toHaveCount(0);
});
