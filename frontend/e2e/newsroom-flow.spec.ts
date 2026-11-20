/**
 * The main newsroom flow, one step per test. Steps run in order in one worker and share the
 * IDs they create; if one fails, the rest are skipped.
 *
 *   source (fixture feed) → open a story in Live News → start an article from it
 *   → edit and ⌘S → publish to the built-in API in one step → unpublish
 */
import path from "node:path";

import { expect, request as apiRequest, test, type APIRequestContext } from "@playwright/test";

import { FEED_URL, WEB_URL } from "./env";
import feed from "./fixtures/feed.json";
import { field, toast } from "./support";

test.describe.configure({ mode: "serial" });

const [keepers] = feed.stories;
const FEED_TITLE = feed.title;
const PUBLISHER = "Harbour Gazette";
const ARTICLE_HEADLINE = "Lighthouse keepers strike over frozen night-shift pay";
const ARTICLE_TEXT =
  "Keepers at three lighthouses around Port Ellery stopped work this week. They say the night-shift " +
  "allowance has not changed in nine years, while the harbour authority says talks are continuing. " +
  "A second day of action is planned if no offer is made.";
const BUILTIN = "Samyabadi public API";

const shared: { articleUrl?: string; slug?: string } = {};

let anonymous: APIRequestContext;
test.beforeAll(async () => {
  // No cookies: proves the public API is public.
  anonymous = await apiRequest.newContext({ baseURL: WEB_URL });
});
test.afterAll(async () => {
  await anonymous.dispose();
});

test("add a source through the Sources dialog and fetch the fixture feed", async ({ page }) => {
  await page.goto("/sources");
  await page.getByRole("button", { name: "Add source" }).first().click();

  const dialog = page.getByRole("dialog", { name: "Add a source" });
  await field(dialog, "Feed URL").fill(`${FEED_URL}/feed.xml`);
  await dialog.getByRole("button", { name: "Test feed" }).click();

  // The probe fetches once without storing and fills the name from the feed title.
  const probe = dialog.getByRole("status");
  await expect(probe).toContainText(FEED_TITLE);
  await expect(probe).toContainText(keepers.title);
  await expect(field(dialog, "Source name")).toHaveValue(FEED_TITLE);

  await field(dialog, "Publisher").fill(PUBLISHER);
  await dialog.getByRole("button", { name: "Add source" }).click();
  await expect(dialog).toBeHidden();
  await expect(toast(page, "Source added")).toBeVisible();

  // Fetch now instead of waiting for the worker.
  await page.getByRole("button", { name: `Actions for ${FEED_TITLE}` }).click();
  await page.getByRole("menuitem", { name: "Fetch now" }).click();
  await expect(toast(page, `${FEED_TITLE}: 4 new, 0 duplicates of 4 entries`)).toBeVisible();
  await expect(page.getByRole("row", { name: new RegExp(FEED_TITLE) })).toContainText("4");
});

test("open a story in Live News and start an article from it", async ({ page }) => {
  await page.goto("/live");
  await page.getByRole("button", { name: keepers.title, exact: true }).click();

  const report = page.getByRole("dialog", { name: keepers.title });
  await expect(report).toBeVisible();
  await report.getByRole("button", { name: "Start article from this" }).click();

  // The draft carries the story's headline and has the story as its source.
  await expect(page).toHaveURL(/\/articles\/[0-9a-f-]{36}/);
  await expect(field(page, "Headline")).toHaveValue(keepers.title);
  shared.articleUrl = page.url();
});

test("edit and save the article with ⌘S, then publish to the built-in API in one step", async ({ page }) => {
  test.skip(!shared.articleUrl, "needs the article from the previous step");
  await page.goto(shared.articleUrl!);

  const body = page.getByRole("textbox", { name: "Article body" });

  await field(page, "Headline").fill(ARTICLE_HEADLINE);
  await body.click();
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.type(ARTICLE_TEXT);
  await expect(page.getByText("Unsaved changes")).toBeVisible();

  // A short vertical video: it may go out as a Facebook Reel and appears on YouTube as a Short.
  // The browser sends it in pieces through the web app's proxy.
  await page.getByTestId("video-file").setInputFiles(path.join(__dirname, "fixtures", "vertical-clip.mp4"));
  await expect(toast(page, "Video uploaded. Save the article to keep it.")).toBeVisible();
  await expect(page.getByText("Can be posted as a Facebook Reel.")).toBeVisible();
  await expect(page.getByText("Appears on YouTube as a Short (vertical, up to 3 minutes).")).toBeVisible();
  await expect(page.getByText("4 s · 360×640 (vertical)", { exact: false })).toBeVisible();

  await page.keyboard.press("ControlOrMeta+S");
  await expect(toast(page, "Saved v2")).toBeVisible();
  await expect(page.getByText("Unsaved changes")).toHaveCount(0);

  // The administrator may approve, so there is nothing to submit or approve first: Publish does it.
  await expect(page.getByRole("button", { name: "Submit for review" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Approve", exact: true })).toHaveCount(0);

  // The built-in site is ticked by default and "Now" is the default time.
  const publishing = page.getByRole("region", { name: "Publish", exact: true });
  await expect(publishing.getByTestId("dest-builtin")).toBeChecked();
  await expect(publishing.getByRole("radio", { name: "Now" })).toBeChecked();
  await publishing.getByRole("button", { name: `Publish to ${BUILTIN}`, exact: true }).click();

  const confirm = page.getByRole("dialog", { name: `Publish now: ${BUILTIN}?` });
  await expect(confirm).toContainText(ARTICLE_HEADLINE);
  await confirm.getByRole("button", { name: "Publish", exact: true }).click();
  await expect(toast(page, `Published to ${BUILTIN}`)).toBeVisible();

  const publications = page.getByRole("region", { name: "Where it is published", exact: true });
  await expect(publications).toContainText(BUILTIN);
  await expect(publications).toContainText("Published");

  // The public API lists it for anyone, without a session.
  const list = await anonymous.get("/api/v1/public/articles");
  expect(list.ok()).toBeTruthy();
  const listed = (await list.json()) as { total: number; items: { headline: string; slug: string }[] };
  const item = listed.items.find((a) => a.headline === ARTICLE_HEADLINE);
  expect(item, "published article is in /api/v1/public/articles").toBeTruthy();
  shared.slug = item!.slug;

  const detail = await anonymous.get(`/api/v1/public/articles/${shared.slug}`);
  expect(detail.ok()).toBeTruthy();
  const published = JSON.stringify(await detail.json());
  expect(published).toContain("night-shift allowance has not changed in nine years");
  expect(published, "the uploaded video is part of the published article").toContain("/media/videos/");

  const rss = await anonymous.get("/api/v1/public/feed.xml");
  expect(await rss.text()).toContain(ARTICLE_HEADLINE);
});

test("unpublish the article from the built-in API", async ({ page }) => {
  test.skip(!shared.articleUrl || !shared.slug, "needs the published article from the previous step");
  await page.goto(shared.articleUrl!);

  const publications = page.getByRole("region", { name: "Where it is published", exact: true });
  await publications.getByRole("button", { name: "Unpublish" }).click();
  const confirm = page.getByRole("dialog", { name: `Remove from ${BUILTIN}?` });
  await expect(confirm).toContainText("disappears from the public API and RSS feed");
  await confirm.getByRole("button", { name: "Unpublish" }).click();
  await expect(toast(page, `Removed from ${BUILTIN}`)).toBeVisible();
  await expect(publications).toContainText("Unpublished");
  await expect(publications.getByRole("button", { name: "Unpublish" })).toHaveCount(0);

  const list = await anonymous.get("/api/v1/public/articles");
  const listed = (await list.json()) as { items: { headline: string }[] };
  expect(listed.items.map((a) => a.headline)).not.toContain(ARTICLE_HEADLINE);
  expect((await anonymous.get(`/api/v1/public/articles/${shared.slug}`)).status()).toBe(404);
  expect(await (await anonymous.get("/api/v1/public/feed.xml")).text()).not.toContain(ARTICLE_HEADLINE);
});
