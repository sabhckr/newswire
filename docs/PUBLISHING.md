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
to post there). You need your own Facebook app, once:

1. At [developers.facebook.com/apps](https://developers.facebook.com/apps/) create an app with
   the use case for managing a Page (posts and Reels).
2. Under *Facebook Login → Settings*, add the **redirect address** that NEWSWIRE shows in the
   *Add destination* dialog to *Valid OAuth Redirect URIs*. It is
   `NEWSWIRE_PUBLIC_BASE_URL` + `/api/v1/publishing/oauth/callback`, so that setting must be
   the address people use (with https in production).
3. In NEWSWIRE: *Publishing → Destinations → Add destination → Facebook Page*, paste the **App
   ID** and **App secret**, then *Save and connect*. Sign in with an account that manages the
   Page and allow every permission (`pages_show_list`, `pages_read_engagement`,
   `pages_manage_posts`). If you manage several Pages, choose one. Add one destination per Page.

Until the app passes Meta's App Review it works for people who have a role on the app (admin,
developer or tester), which is enough when your own team runs NEWSWIRE.

| Choice | What is posted |
|---|---|
| **Post** | the caption (or headline and excerpt) with the link to the article on your website when it is published there; otherwise the featured image as a photo post; otherwise text only |
| **Reel** | the article's video with the caption. Facebook requires a vertical video of 3–90 seconds; NEWSWIRE checks this before queuing |

Facebook has no drafts. *Unpublish* deletes the post (Facebook cannot hide a post through the
API). Updating a published article updates the post's text.

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
