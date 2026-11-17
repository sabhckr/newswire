/**
 * Layout and theme checks. Runs after the newsroom flow so the pages show real rows
 * (a source, a headline with matches, a published-then-unpublished article).
 */
import { expect, test, type Page } from "@playwright/test";

import { settle } from "./support";

/** Background colours from app/globals.css (--bg in :root and .dark). */
const LIGHT_BG = "rgb(246, 247, 249)";
const DARK_BG = "rgb(13, 17, 23)";

/** Elements that stick out past the right edge of the viewport, for a readable failure message. */
async function horizontalOverflow(page: Page) {
  return page.evaluate(() => {
    const doc = document.documentElement;
    const width = doc.clientWidth;
    const offenders: string[] = [];
    for (const el of Array.from(document.body.querySelectorAll<HTMLElement>("*"))) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.right <= width + 1) continue;
      // Content inside a horizontally scrollable box (wide tables) does not widen the page.
      let clipped = false;
      for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
        const ox = getComputedStyle(p).overflowX;
        if (ox !== "visible") {
          clipped = true;
          break;
        }
      }
      if (clipped) continue;
      const id = el.id ? `#${el.id}` : "";
      const cls = typeof el.className === "string" && el.className ? `.${el.className.trim().split(/\s+/).slice(0, 4).join(".")}` : "";
      offenders.push(`${el.tagName.toLowerCase()}${id}${cls} (right edge ${Math.round(r.right)} px)`);
      if (offenders.length >= 5) break;
    }
    return { scrollWidth: doc.scrollWidth, clientWidth: width, offenders };
  });
}

test.describe("phone width", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("no sidebar route scrolls sideways at 375 px", async ({ page }) => {
    // The sidebar is hidden below the lg breakpoint; its links live in the navigation drawer.
    await page.goto("/");
    await page.getByRole("button", { name: "Open navigation" }).click();
    const drawer = page.getByRole("dialog", { name: "Navigation" });
    const links = drawer.getByRole("navigation", { name: "Main" }).getByRole("link");
    await expect(links.first()).toBeVisible();
    const routes = await links.evaluateAll((els) => els.map((e) => e.getAttribute("href") ?? ""));
    expect(routes.length, "sidebar routes found in the navigation drawer").toBeGreaterThanOrEqual(12);
    expect(await horizontalOverflow(page), "navigation drawer").toMatchObject({ offenders: [] });
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();

    for (const route of routes) {
      await test.step(route, async () => {
        await page.goto(route);
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
        await settle(page);
        const o = await horizontalOverflow(page);
        expect
          .soft(o.scrollWidth, `${route} is ${o.scrollWidth} px wide on a ${o.clientWidth} px screen. Widest: ${o.offenders.join("; ")}`)
          .toBeLessThanOrEqual(o.clientWidth);
      });
    }
  });
});

test("theme toggle cycles light → dark → system, persists, and follows the OS in system mode", async ({ page }) => {
  const html = page.locator("html");
  const toggle = page.getByRole("button", { name: /^Theme: / });
  const savedTheme = () =>
    page.waitForResponse((r) => r.url().endsWith("/api/v1/auth/me") && r.request().method() === "PATCH" && r.ok());

  // A new account follows the operating system.
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/");
  await expect(toggle).toHaveAttribute("aria-label", "Theme: system. Switch to light");
  await expect(html).toHaveClass(/\bdark\b/);
  await expect(page.locator("body")).toHaveCSS("background-color", DARK_BG);
  await page.emulateMedia({ colorScheme: "light" });
  await expect(html).toHaveClass(/\blight\b/);
  await expect(page.locator("body")).toHaveCSS("background-color", LIGHT_BG);

  // Light ignores a dark OS.
  await page.emulateMedia({ colorScheme: "dark" });
  let saved = savedTheme();
  await toggle.click();
  await saved;
  await expect(toggle).toHaveAttribute("aria-label", "Theme: light. Switch to dark");
  await expect(html).toHaveClass(/\blight\b/);
  await expect(html).not.toHaveClass(/\bdark\b/);
  await expect(page.locator("body")).toHaveCSS("background-color", LIGHT_BG);

  // Dark ignores a light OS, survives a reload and is stored on the account.
  await page.emulateMedia({ colorScheme: "light" });
  saved = savedTheme();
  await toggle.click();
  await saved;
  await expect(toggle).toHaveAttribute("aria-label", "Theme: dark. Switch to system");
  await expect(html).toHaveClass(/\bdark\b/);
  await expect(page.locator("body")).toHaveCSS("background-color", DARK_BG);
  await page.reload();
  await expect(toggle).toHaveAttribute("aria-label", "Theme: dark. Switch to system");
  await expect(page.locator("body")).toHaveCSS("background-color", DARK_BG);
  const me = await page.request.get("/api/v1/auth/me");
  expect(JSON.stringify(await me.json())).toContain('"theme_preference":"dark"');

  // Back to system: follows the OS again (and leaves the account as it started).
  saved = savedTheme();
  await toggle.click();
  await saved;
  await expect(toggle).toHaveAttribute("aria-label", "Theme: system. Switch to light");
  await expect(html).toHaveClass(/\blight\b/);
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(html).toHaveClass(/\bdark\b/);
  await expect(page.locator("body")).toHaveCSS("background-color", DARK_BG);
});
