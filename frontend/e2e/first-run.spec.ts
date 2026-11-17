import fs from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

import { ADMIN, AUTH_FILE } from "./env";

test("first visit leads to /setup, which creates the administrator and signs them in", async ({ page }) => {
  // A fresh install has no users: the app sends the visitor to the login page, which sends them to setup.
  await page.goto("/");
  await expect(page).toHaveURL(/\/setup$/);
  await expect(page.getByText("Set up NEWSWIRE")).toBeVisible();

  await page.getByLabel("Your name").fill(ADMIN.name);
  await page.getByLabel("Email").fill(ADMIN.email);
  await page.getByLabel("Password", { exact: true }).fill(ADMIN.password);

  // Client-side validation catches a mismatched confirmation before anything is sent.
  await page.getByLabel("Repeat password").fill(`${ADMIN.password}-typo`);
  await page.getByRole("button", { name: "Create administrator" }).click();
  await expect(page.getByText("Passwords do not match")).toBeVisible();

  await page.getByLabel("Repeat password").fill(ADMIN.password);
  await page.getByRole("button", { name: "Create administrator" }).click();

  await expect(page).toHaveURL(/\/sources\?welcome=1$/);
  await expect(page.getByText("Welcome to NEWSWIRE")).toBeVisible();
  await expect(page.getByRole("button", { name: `Account: ${ADMIN.name}` })).toBeVisible();

  const status = await page.request.get("/api/v1/auth/setup-status");
  expect(await status.json()).toMatchObject({ needs_setup: false });

  fs.mkdirSync(path.dirname(AUTH_FILE), { recursive: true });
  await page.context().storageState({ path: AUTH_FILE });
});
