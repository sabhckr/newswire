# Publishing

NEWSWIRE publishes only what a person has approved. This page covers the article workflow, the
checks that can block publishing, the three destination types and how a receiving website
verifies webhooks.

## Article workflow

```
draft ─► in_review ─► approved ─► (scheduled ─►) published ─► unpublished
   ▲          │            │                                      │
   └──────────┴────────────┴─────────── back to draft ◄───────────┘
any state except published ─► archived ─► draft
```

| Move | Needs |
|---|---|
| draft ↔ in_review, back to draft, archive | `articles:write` (researcher, editor, admin) |
| → approved | `articles:approve` (editor, admin) |
| approved → scheduled / published, unpublish | `articles:publish` (editor, admin) |

When someone without approval rights changes the text, headline or images of an approved or
scheduled article, it goes back to *in review* and any scheduled publication is cancelled. Edits by
an editor or admin keep the approval. Every save creates a version
in the article's history. Saves carry the version number, so two people editing at once get a
conflict instead of overwriting each other.

## Pre-publication checks

Checks run for each destination when you publish, and the editor shows them as you work.
**Errors block publishing**:

| Code | Meaning |
|---|---|
| `headline_missing` | No headline |
| `body_too_short` | Fewer than 20 words |
| `template_markers` | `[EDITOR: …]` instructions from the article template are still in the text (switchable under *Publishing → Policy*) |
| `unknown_license` | An image's licence is unknown or unrecognised |
| `non_commercial` | A non-commercial (NC) image on a destination marked commercial |
| `no_derivatives` | A no-derivatives (ND) image marked as modified (cropped, edited) |
| `attribution_missing` | The licence requires attribution but none is recorded |
| `not_approved` | An image has not been approved in the Image Library |

Warnings (`no_sources`, `share_alike`, `commercial_undocumented`, `not_downloaded`) are shown
but do not block. *Publish as draft* skips the text checks so you can preview on the remote site,
but image licence errors block drafts too.

## Destinations

Manage them under *Publishing → Destinations* (admin). Mark a destination **commercial** if
the site carries advertising or is otherwise commercial; NC images are then refused.

### Built-in public API

Always present and cannot be deleted. Publishing makes the article visible, without signing in, at:

| Endpoint | Returns |
|---|---|
| `GET /api/v1/public/articles?page=&page_size=` | published articles, newest first |
| `GET /api/v1/public/articles/{slug}` | one article: headline, subtitle, excerpt, HTML body, featured image with credit and licence, tags, category, source references, timestamps |
| `GET /api/v1/public/feed.xml` | RSS 2.0 feed of the latest articles |

Use these to drive a static site generator or any front end. Links use `NEWSWIRE_PUBLIC_BASE_URL`.

### Website (admin login)

The easiest way to add **any** news website: give NEWSWIRE the site's own admin login, once.
*Publishing → Destinations → Add destination → Website (admin login)*:

* **Admin login page**: the address where you sign in, e.g. `https://example.com/admin/login`
  or `https://example.com/wp-login.php`.
* **Admin email or username** and **password**. The password is encrypted at rest (like every
  other credential) and never shown again; NEWSWIRE uses it to sign in again whenever the
  website logs it out, so publishing keeps working.

NEWSWIRE signs in once when you save, and finds out what the site is:

* **WordPress** — it creates an application password for itself through the signed-in session
  and then publishes exactly like the *WordPress* destination below (drafts, updates, unpublish,
  images and video). If the application password is ever revoked, it makes a new one.
* **Any other admin panel** — NEWSWIRE uses a *robot browser*: its own pure-Python engine
  (`backend/app/robot`) drives the Google Chrome, Microsoft Edge, Brave or Chromium that is
  already installed on the computer. Nothing extra is installed or downloaded, and your own
  browser profile is never touched (each run uses a throwaway profile). Check it with
  `python -m app.cli robot-check`; to use a particular browser set `NEWSWIRE_BROWSER_PATH`.
  Click **Teach** on the destination card: a browser window opens on the
  computer running NEWSWIRE, signs in, and a blue bar asks you to (1) open the page for writing a
  new post and press *This is the page*, then click (2) the headline box, (3) the article text
  box, optionally the photo upload, excerpt, category and tags, and finally (4) the Publish
  button. Your clicks are only recorded; nothing is published while teaching. If the site asks
  for a captcha or a one-time code, finish signing in in that window. Closing the window (or
  pressing *Cancel*) stops teaching without changing anything.

  From then on NEWSWIRE fills in that form and presses Publish by itself, and records the link
  of the new post when the site shows one. It keeps the browser's sign-in, so it rarely has to
  log in again. Limits: no drafts, and *Unpublish* has to be done in the website's own admin
  panel. If the site's admin panel changes, click *Teach again*. A screenshot of any failed
  attempt is kept in `storage/logs/robot/`. The robot waits for a Publish button that is greyed
  out while a photo uploads, answers the site's "are you sure?" boxes with OK, and passes on the
  site's own error message when a post is refused.

  The *Advanced* setting **Article text** chooses how the body is put in the text box:
  *Automatic* (formatted for rich editors, plain text for plain boxes), *Formatted (HTML)* or
  *Plain text*.

In Docker the robot browser runs headless, so teaching (which needs a window) must be done on a
desktop install; WordPress sites work everywhere.

### WordPress

Uses the WordPress REST API with an **application password** (WordPress → Users → Profile →
Application Passwords). Enter the site URL, the username and the application password. The
password is encrypted at rest and never shown again. *Test connection* calls `/users/me`.

* Publishing creates the post (or updates the existing one) with title, content, excerpt and
  slug, and uploads the featured image to the media library with its caption and credit.
* *Publish as draft* creates a WordPress draft. *Unpublish* sets the post back to draft. It is
  never deleted.
* *Sync status* reads the post's current state, for example if someone trashed it in WordPress.
* An article's uploaded video is added to the WordPress media library once and placed at the top
  of the post. WordPress limits upload size (`upload_max_filesize` in PHP); a refusal is shown
  with that hint.

### Facebook Page

Posts to a Facebook **Page** you manage (not a personal profile; Facebook does not allow apps
to post there).

**One-time setup (an administrator): the Meta app.** Facebook and Instagram both sign in through
one Facebook app that belongs to your newsroom. Set it up once under *Settings → Facebook &
Instagram*:

1. At [developers.facebook.com/apps](https://developers.facebook.com/apps/) create an app with
   the use case for managing a Page (posts and Reels). For Instagram also add *Instagram API
   with Facebook Login*.
2. Under *Facebook Login → Settings*, add the **redirect address** that NEWSWIRE shows on that
   settings tab to *Valid OAuth Redirect URIs*. It is `NEWSWIRE_PUBLIC_BASE_URL` +
   `/api/v1/publishing/oauth/callback`, so that setting must be the address people use (with
   https in production).
3. Paste the **App ID** and **App secret** into *Settings → Facebook & Instagram* and save. The
   secret is encrypted and never shown again. Changing the App ID disconnects every Page and
   account that signed in with the old app.

Until the app passes Meta's App Review it works for people who have a role on the app (admin,
developer or tester), which is enough when your own team runs NEWSWIRE.

**Adding a Page:** *Publishing → Destinations → Add destination → Facebook Page → Save and sign
in*. Sign in with an account that manages the Page and allow every permission
(`pages_show_list`, `pages_read_engagement`, `pages_manage_posts`). If you manage several Pages,
choose one. Add one destination per Page. (Destinations created before the shared app keep their
own App ID and secret, editable in the destination.)

| Choice | What is posted |
|---|---|
| **Post** | the caption (or headline and excerpt) with the link to the article on your website when it is published there; otherwise the featured image as a photo post; otherwise text only |
| **Reel** | the article's video with the caption. Facebook requires a vertical video of 3–90 seconds; NEWSWIRE checks this before queuing |

Facebook has no drafts. *Unpublish* deletes the post (Facebook cannot hide a post through the
API). Updating a published article updates the post's text.

### Instagram account

Posts to an Instagram **Professional** account (Business or Creator) that is linked to a
Facebook Page you manage — Instagram only lets apps publish to those. In the Instagram app:
*Settings → Account type and tools → Switch to professional account*, then link it to the Page in
*Accounts Center*.

*Add destination → Instagram account → Save and sign in*: sign in with Facebook (the account
that manages the linked Page), allow the permissions (`instagram_basic`,
`instagram_content_publish`, `pages_show_list`, …), and choose the Instagram account if there is
more than one. Uses the Meta app from *Settings → Facebook & Instagram*.

| Choice | What is posted |
|---|---|
| **Post** | the featured image with the caption (Instagram shows no links in captions) |
| **Reel** | the article's video with the caption; same video rules as Facebook Reels |

Instagram fetches the image or video from a **public address**. NEWSWIRE uses the image's
original public link when it has one (e.g. Wikimedia or Openverse); for an uploaded image or
video, NEWSWIRE itself must be reachable from the internet over https
(`NEWSWIRE_PUBLIC_BASE_URL`). On a laptop install that is not the case, so use images with a
public source, or post from a server install.

Instagram has no drafts, and apps cannot delete Instagram posts: remove a post in the Instagram
app. Updating a published article does not change the Instagram post.

### YouTube channel

Uploads the article's video to your channel. You need a Google Cloud project, once:

1. In the [Google Cloud console](https://console.cloud.google.com/apis/library/youtube.googleapis.com)
   create a project and enable **YouTube Data API v3**.
2. Set up the OAuth consent screen (External) and **publish** it. While it is in *Testing*,
   Google ends the connection after 7 days and you must connect again.
3. *Credentials → Create credentials → OAuth client ID → Web application*; add the redirect
   address from the dialog as an *Authorized redirect URI*.
4. In NEWSWIRE add a **YouTube channel** destination with the Client ID and Client secret, then
   *Save and connect* and pick the channel.

* Title, description (caption, or excerpt and link to your article), tags and visibility
  (public, unlisted, private) come from the article and the Publish card. Vertical videos up to
  3 minutes become Shorts.
* Until Google audits the project, YouTube keeps every API upload **private**. NEWSWIRE says so
  on the publication; the fix is Google's audit form, not a setting.
* The free quota is about 6 uploads a day (each costs 1,600 of 10,000 units). A
  `quotaExceeded` error is not retried; try the next day.
* *Unpublish* makes the video private; publishing again sets the visibility you choose then.

### Videos

Upload a video in the editor's **Video** card (MP4 or MOV, up to `NEWSWIRE_MAX_VIDEO_BYTES`,
default 1 GB). The browser sends it in 8 MB pieces through `/api/v1/videos/uploads` and
continues after a dropped connection. Scripts may instead POST the whole file to
`/api/v1/videos` when they talk to the API directly (not through the web app's proxy, which cuts
request bodies at 10 MB). Videos are not transcoded: upload what the platforms accept (H.264
and AAC in MP4 is safest).

### Signed webhook

NEWSWIRE POSTs JSON to your URL:

```http
POST /your/endpoint HTTP/1.1
Content-Type: application/json
X-Newswire-Event: article.published
X-Newswire-Delivery: 3f6c…:4:article.published
X-Newswire-Signature: sha256=5d41402abc4b2a76b9719d911017c592…
```

```json
{
  "event": "article.published",
  "delivery_id": "…",
  "article": {
    "id": "…", "slug": "…", "headline": "…", "subtitle": "…", "excerpt": "…",
    "seo_title": "…", "seo_description": "…", "category": "…", "tags": ["…"],
    "html": "<article>…full rendering…</article>",
    "body_html": "<p>…body only…</p>",
    "featured_image": {"url": "…", "caption": "…", "credit": "…", "license": "CC BY 4.0",
                       "license_url": "…", "source_page_url": "…"},
    "sources": [{"title": "…", "url": "…", "publisher": "…"}],
    "version": 4, "published_at": "…", "updated_at": "…"
  }
}
```

Events: `article.published`, `article.updated` (re-publishing a live article), `article.draft`,
`article.unpublished` (body carries only `id` and `slug`) and `ping` (*Test connection*).
`X-Newswire-Delivery` is stable for a given article version and event, so you can use it to
ignore repeats. Reply with any 2xx status. Optionally return `{"id": "…", "url": "…"}` and
NEWSWIRE stores them as the remote ID and URL.

**Verify the signature** with the shared secret over the raw request body before parsing it:

```python
import hashlib, hmac

def verify(secret: str, raw_body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])
```

```js
import crypto from "node:crypto";
function verify(secret, rawBody, header) {
  if (!header?.startsWith("sha256=")) return false;
  const expected = crypto.createHmac("sha256", secret).update(rawBody).digest("hex");
  const got = header.slice(7);
  return got.length === expected.length && crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(got));
}
```

## Publishing to several places

The Publish card sends one request for every ticked destination
(`POST /api/v1/articles/{id}/publish-many`). All destinations are checked first; if any cannot
take the article, nothing is queued. Facebook and YouTube jobs wait up to 30 minutes for the
website job in the same request so their posts can link to the article. Uploads that take
minutes (YouTube, Reels, WordPress with a video) run in the background and the card follows
them. A scheduled request keeps the article *scheduled* until every destination has run.

## The publishing queue

* Each request becomes a job with an **idempotency key** (article, destination, action, mode,
  article version, schedule). Double-clicking or retrying the same request returns the existing
  job instead of publishing twice.
* *Publish now* runs immediately. Scheduled jobs wait for their time. The worker runs anything
  pending.
* Failures that may be temporary (network errors, HTTP 5xx and 429) are retried with exponential
  backoff (1, 2, 4, 8 minutes; 5 attempts). Permanent failures (bad credentials, HTTP 4xx) stop
  at once. After the final failure the article returns from *scheduled* to *approved* and a
  notification is raised.
* *Retry* gives a failed or cancelled job three more attempts. On a job waiting out its backoff,
  it runs the job now. Pending jobs can be cancelled.
* Every request, success and failure is in the audit log.

## Auto-publishing

Off by default. It needs **both** the admin policy (*Publishing → Policy → Allow
auto-publish*) **and** *Auto-publish* on the destination. Then approving an article queues it
for those destinations. The same checks apply: an article with an unknown-licence,
non-commercial-on-commercial, no-derivatives-modified or unapproved image is skipped, and the
skip is written to the audit log.
