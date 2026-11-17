# User guide

A walk through NEWSWIRE for editors and researchers. What you can do depends on your role (see
[SECURITY.md](SECURITY.md#roles)). Buttons you are not allowed to use are hidden.

## Getting around

* The **sidebar** lists every area. On a phone it opens from the ☰ button.
* **Search** (header, or ⌘K / Ctrl+K) looks through news, headlines, clusters, articles and
  sources at once, and doubles as a command palette for jumping to pages.
* **Go-to keys:** press `g` then a letter: `o` Overview, `l` Live News, `h` My Headlines,
  `r` Research, `c` Clusters, `s` Sources, `i` Images, `a` Articles, `p` Publishing,
  `y` Analytics, `v` Activity, `,` Settings. On the Headlines and Articles lists, `n` creates a
  new one. In the editor, ⌘S / Ctrl+S saves.
* The header shows **collection status** (green when the worker is running, amber when sources
  are failing, red when the worker has stopped), **notifications**, a **New** menu, the theme
  switch (light, dark or follow the system) and your account menu.

## Overview

Today's numbers from midnight in your time zone: reports collected, new matches, active
headlines, publishers, matches waiting for review, drafts and published articles. Below them
are collection volume over two days, your active headlines with their unreviewed matches, and
the latest news.

## Sources

The feeds NEWSWIRE collects from. Only add feeds you are allowed to monitor.

* **Add source:** paste the feed URL and press **Test feed**. NEWSWIRE fetches it once and shows
  what it would collect, without storing anything. Then fill in the publisher, where the
  publisher is based (this is never treated as where stories happen), language, category,
  polling interval and priority. For JSON APIs, map the fields (title, link, date, summary).
* **Import / Export** a list as JSON, or import OPML from another feed reader.
* Each source's page shows health over 24 hours, 7 days and 30 days, every fetch with its HTTP
  status and timing, the configuration and its latest stories. **Fetch now**, **Disable** and
  **Edit** are there too.
* A failing source backs off automatically: its interval doubles after each consecutive failure
  (5, 10, 20 … minutes for a 5-minute source) up to six hours, with a little jitter. A source
  disallowed by robots.txt is not fetched. NEWSWIRE re-checks robots.txt at most hourly and
  resumes only if the publisher allows it.

## Live News

Every collected report, newest first, updating every 20 seconds. A banner offers new reports
without moving the list under you, or switch on **Auto-refresh**.

* Filter by text, publisher, country, language, matched or unmatched, and date range, and sort
  by published or retrieved time. Duplicates are listed by default. The **Duplicates** filter can
  hide them or show only them. The filters are part of the URL,
  so you can bookmark or share a view.
* Click a report to open its detail panel: summary, link to the original, places (with role,
  method and confidence), other feeds it appeared in, duplicates, its cluster and the headlines
  it matched. Researchers can correct a location and mark the report verified or archived.

## My Headlines

A headline (assignment) is a story you are researching. NEWSWIRE scores every new report
against every active headline. [MATCHING.md](MATCHING.md) explains the scoring.

* **Keywords:** *primary* terms are the core of the story. *Synonyms* are other names and
  spellings. *Required phrases* must appear. *Secondary* terms only add context. *Excluded*
  terms reject a report. Each chip can have its own points.
* **Places:** countries, regions, cities or custom areas. *Boost* adds points. *Require* only
  accepts reports about those places.
* **Sources:** favour particular sources, or only accept reports from them.
* **Timing and threshold:** date range, minimum score and priority.
* The **live preview** re-scores recent reports as you type, so you can see what the rules
  would catch, and what they would just miss, before saving.
* Saving scores the last 7 days of reports. Pause a headline to stop new matches without losing
  the old ones. Archive it when the story is finished.

## Research Workspace

One workspace per headline:

* **Matches:** each report with its score and the reasons. Mark it *verified*, *rejected* or
  *archived*, one at a time or in bulk, with notes. Verified reports become references when you
  start an article.
* **Timeline**, **Publishers** (who reported first) and **Geography** (reports per country and
  role). Click a publisher or country to filter the matches.
* **Clusters** related to the story, **Images** saved for it (and a search of Openverse and
  Wikimedia Commons), and **Notes & checklist**.
* **Start article** creates a draft with a factual template and the verified reports as
  references. Replace every `[EDITOR: …]` instruction with your own reporting. Publishing is
  blocked while any remain.

## Story Clusters

Reports from different publishers about the same story, grouped automatically. Open a cluster
to see each report with how it joined. You can rename it, merge others into it, split reports
out, remove or add reports, lock it so automatic grouping leaves it alone, and archive it.

## Image Library

* **Search repositories:** Openverse or Wikimedia Commons. Each result shows its licence and
  warnings. Results whose licence blocks use cannot be saved.
* **Save for review** stores the candidate. An editor **approves** it, which downloads the file
  and makes a thumbnail, or **rejects** it with a note.
* **Upload** your own images. You must choose the licence under which you may use the image.
* The detail panel shows provenance, licence terms and a rights check for commercial use, and
  lets you correct title, creator and attribution.

## Articles

* The list has tabs by status with counts, search and sorting.
* The **editor** has the headline, subtitle, body (headings, lists, quotes, links), slug,
  excerpt, SEO title and description, category, tags, featured image with caption and credit,
  and source references. **Preview** shows the article as readers will see it, at desktop or
  phone width.
* Save with the button or ⌘S. If someone else saved in the meantime, you get a conflict
  warning with **Load latest**, not a silent overwrite.
* The side panel's **Workflow** tab moves the article through
  *draft → in review → approved*, shows the pre-publication checks and holds the publishing
  controls. **History** lists every saved version.

## Publishing

* **Publish** an approved article from the editor: choose a destination, then *publish now*,
  *schedule* or *send as draft*. NEWSWIRE always asks you to confirm.
* **Publishing → Queue:** every job with its status, attempts and errors. Retry or cancel jobs,
  or run the queue now.
* **Destinations** (admin): the built-in public API, WordPress sites and webhooks. Use **Test
  connection** first.
* **Policy** (admin): auto-publishing (off by default) and the template-marker block.
* **Webhook format:** the payload and how to verify signatures. See [PUBLISHING.md](PUBLISHING.md).

## Analytics

Reports for any period (in UTC days): news volume and duplicates, coverage by country,
publishers, categories, editorial throughput, precision per headline, source reliability and
publishing activity. Each has a **CSV** button.

## Activity Logs

* **Audit trail:** who did what and when, searchable and filterable, with CSV export. Click a
  summary to open what it refers to.
* **Fetch history:** every request the worker made, with result, HTTP status, timing and counts.

## Settings

* **Profile & theme:** your name, theme and password. Changing your password signs out your other
  sessions.
* **Matching & scoring**, **Dedup & clustering**, **Collection & backups** (admin): the
  thresholds described in [MATCHING.md](MATCHING.md) and the backup schedule.
* **Categories** and **Users & roles** (admin): create accounts with a starting password, set
  roles, deactivate accounts.

## System (admin)

Open it from the status pill in the header. It shows versions, the database and migration, the
worker and each scheduled job, fetch counts, disk use, failing sources, recent logs (with a
redacted export) and **Backups**: create, verify, download or delete. Restoring is done from the
command line ([RECOVERY.md](RECOVERY.md)).
