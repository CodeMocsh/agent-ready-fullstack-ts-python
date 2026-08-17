import { expect, test } from "@playwright/test";

test("adds a task against the mock backend", async ({ page }) => {
  await page.goto("./");

  await expect(page.getByRole("heading", { name: "Tasks" })).toBeVisible();
  await expect(page.getByText(/Mock mode/)).toBeVisible();
  await expect(page.getByText("Read AGENTS.md")).toBeVisible();

  await page.getByLabel("New task title").fill("Take a screenshot");
  await page.getByRole("button", { name: "Add" }).click();

  await expect(page.getByText("Take a screenshot")).toBeVisible();
});
