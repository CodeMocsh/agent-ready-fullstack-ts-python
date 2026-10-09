import { CANARY, nextClientEvent } from "./client-events";
import { expect, test } from "./signed-in";

test("an error nothing caught is recorded by its class, never by what it says", async ({
  page,
}) => {
  const said: string[] = [];
  page.on("console", (message) => said.push(message.text()));
  await page.goto("./");
  await page.waitForLoadState("networkidle");

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

  expect(thrown).toMatchObject({
    events: [{ kind: "uncaught", error: "TypeError", route: expect.stringMatching(/^\//) }],
  });
  expect(rejected).toMatchObject({ events: [{ kind: "uncaught", error: "RangeError" }] });
  expect(JSON.stringify([thrown, rejected])).not.toContain(CANARY);
  expect(said.some((line) => line.includes("client event"))).toBe(true);
});
