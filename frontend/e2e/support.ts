import type { Locator, Page } from "@playwright/test";

/**
 * A form control by its visible label. Required fields render the label as "Label *", and
 * Playwright's default substring match would also catch longer labels ("Publisher country").
 */
export function field(scope: Page | Locator, label: string): Locator {
  const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return scope.getByLabel(new RegExp(`^${escaped}( \\*)?$`));
}

/** A Sonner toast containing `text`. */
export function toast(page: Page, text: string | RegExp): Locator {
  return page.locator("[data-sonner-toast]").filter({ hasText: text });
}

/** Wait until the page has no requests in flight (pages poll on long intervals, so this settles). */
export async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle");
}
