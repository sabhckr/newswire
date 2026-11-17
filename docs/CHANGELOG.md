# Changelog

All notable changes to NEWSWIRE. Versions follow [semantic versioning](https://semver.org/).
Releases with a migration that drops or rewrites data are marked **⚠ data migration**.

## Unreleased

### Testing
- Playwright browser suite (`cd frontend && npm run test:e2e`): first-run setup, adding a source
  from a local fixture feed, a headline with its live preview, verifying a match, starting,
  editing (⌘S), approving, publishing and unpublishing an article, a 375 px overflow check of
  every sidebar page and theme switching. It starts its own fixture feed, API (throwaway SQLite)
  and production web build on 127.0.0.1. See the README.
- `NEWSWIRE_NEXT_DIST_DIR` sets the Next.js build directory; the browser suite builds into
  `.next-e2e` so a running dev server is not disturbed.

## 0.10.0 — 2026-11-17

Bangladesh focus: Bangla text handling, a Bangladesh gazetteer, news sitemaps and a source pack
of Bangladeshi newspapers. No migration; existing installations pick up the new gazetteer data
on their next start.

### Bangla language
- Matching, deduplication and clustering understand Bangla: zero-width joiners removed, Bangla
  digits read as ASCII, case endings and plural markers stripped for the inflection level
  (`ঢাকা` matches `ঢাকায়`, `পদ্মা সেতু` matches `পদ্মা সেতুর`), Bangla stop words, Bangla slugs.
- Bangla datelines (`ঢাকা, ১৭ নভেম্বর (বাসস) :`) and correspondent credits (`রাজশাহী
  প্রতিনিধি`, `Staff Correspondent, Bogura`) give a report its event location.
- Bengali system fonts in the interface and the article preview; Bangla article previews are
  marked `lang="bn"`.

### Geography
- Bangladesh's 8 divisions and 64 districts (ISO 3166-2:BD) and 28 towns, with Bangla names,
  older spellings and a division → district → town hierarchy.
- Bangla names for continents, 59 countries and 41 foreign cities; inflected Bangla place names
  (`ঢাকায়`, `চট্টগ্রামের`) are recognised.
- The GeoNames import keeps the curated Bangladesh regions and does not duplicate its districts.

### Collection
- News sitemaps (Google News sitemap format) as a source type, for publishers without RSS.
  Sitemap indexes are followed to their newest news sitemap. Plain sitemaps can be used with
  page metadata lookup, which now also supplies a missing headline: only story pages count
  (not section or tag pages), the site name is dropped from the page title, entries older than
  two days are not looked up, pages without a usable headline are not asked again for six hours,
  and lookups to one site are spaced by the per-host delay.
- *Find feeds on a website*: checks a site's feed links and the news sitemaps in its robots.txt
  and tests each one. When nothing else works it tries the site's other sitemaps and reads two
  of the newest pages to show the headlines page lookup would give. Each result shows sample
  headlines, and feeds whose newest entry is more than three days old are marked.
- Source packs: bundled lists of public feeds that can be added in one step
  (Sources → Source packs, or `python -m app.cli import-sources --pack NAME`).
- **Bangladeshi newspapers** pack: 68 feeds (57 Bangla, 11 English; 42 RSS,
  20 news sitemaps, 6 plain sitemaps read with page lookup), each fetched and parsed on
  17 November 2026 with robots.txt checked. Includes Prothom Alo, Jugantor, Samakal, Ittefaq,
  Manab Zamin, Amader Shomoy, Amar Desh, bdnews24, The Daily Star, Dhaka Tribune, New Age, The
  Business Standard, BSS, regional papers from Chattogram, Sylhet, Bogura, Jashore, Rajshahi and
  Cox's Bazar, TV news sites, BBC News Bangla and DW Bangla.
- Feed dates in non-standard formats (`Tue, 17 Nov 2026, 11:54:23 +0600`) are read. A
  publication time more than 10 minutes in the future is corrected when it is the source
  country's local time labelled as UTC (Bangladesh and other countries with a fixed offset ahead
  of UTC), and set aside otherwise; the time as given is kept. Sitemap dates without a time are
  not treated as publication times.
- A report's language follows its script when the feed declares the wrong one (Bangla
  WordPress feeds that say `en-US`, English feeds that say `bn`).

### Search
- News search understands Bangla: each Bangla word also finds the words that start with it or
  its stem (`ঢাকা` and `ঢাকায়` both find `ঢাকায়`, `ঢাকার`), whichever way `য়`, `ড়` and `ঢ়`
  are encoded, and common function words are ignored. `-word` excludes. On the local data set
  `নির্বাচনে` went from 42 to 105 reports.

### Analytics
- Reports count days in the viewer's time zone instead of UTC.

## 0.9.0 — 2026-11-17

First complete build, covering milestones M0–M9 of the [architecture plan](ARCHITECTURE.md#9-milestones).
Migration `0001` (initial schema) only creates tables.

### Platform
- FastAPI backend with versioned REST API under `/api/v1`, OpenAPI docs at `/api/docs`.
- Separate worker process (`python -m app.worker`) for collection, the publishing queue,
  scheduled backups and housekeeping, with a heartbeat shown in the UI.
- PostgreSQL (production) and SQLite (development/tests) through SQLAlchemy 2 and Alembic.
  PostgreSQL full-text search with a GIN index for news search.
- Administration CLI: `migrate`, `bootstrap`, `create-admin`, `reset-password`, `backup`,
  `verify-backup`, `restore`, `import-geonames`.
- Docker Compose stack (PostgreSQL 17, API, worker, web, optional Caddy HTTPS), Nginx example
  and systemd units.

### Collection
- RSS, Atom and JSON feed sources with field mapping for JSON, per-source polling intervals,
  conditional requests, robots.txt, per-host politeness delay, exponential backoff and health
  tracking. Source test, import/export (JSON and OPML) and a fetch log.
- SSRF-guarded HTTP client: connect-time address checks, per-hop redirect validation, size caps.
- Deduplication levels 1–4 (URL, canonical URL, content hash, near-identical headline).
- Geography: built-in gazetteer (202 countries, 464 cities) with publisher / event / article /
  mentioned roles, confidence and provenance; optional GeoNames import; custom areas.
- Deterministic story clustering with editor rename, merge, split, lock and archive.

### Research
- Editorial assignments with required, primary, synonym, secondary and excluded phrases,
  per-keyword weights, places, sources, category, language and date range; live preview of
  what an assignment would match; duplicate, pause, archive and bulk actions.
- Explainable relevance scoring (exact, inflection, token and fuzzy levels) with configurable
  weights, a per-match breakdown and verified/rejected review.
- Research workspace per assignment: matches, timeline, publishers, geography, clusters,
  images, notes and checklist; *Start article* drafts a template that cites the verified reports.

### Images
- Openverse and Wikimedia Commons search with licence normalisation (CC0, PDM, CC BY family),
  warnings for unknown, NC, ND and attribution-missing cases, local download, thumbnails and
  editor approval. Uploads require a licence.

### Articles and publishing
- Rich-text editor (TipTap) with explicit save (⌘/Ctrl+S), a leave-page warning, versions and
  optimistic concurrency, references, tags, SEO fields, featured image and credits, sanitised
  HTML and a serif preview at desktop and phone widths.
- Workflow draft → in review → approved → scheduled/published → unpublished, with role checks.
- Pre-publication checks, including image licence rules for commercial destinations.
- Destinations: built-in public API and RSS feed, WordPress (application passwords) and
  HMAC-signed webhooks. Idempotent job queue with retries and backoff, scheduling, retry/cancel,
  status sync. Auto-publishing is off by default.

### Operations
- Analytics: volume, countries, publishers, categories, editorial throughput, headline
  precision, source reliability and publishing activity, all exportable as CSV.
- Append-only audit trail and fetch history with CSV export, notifications.
- System page: versions, worker, jobs, storage, failing sources, logs (redacted export),
  backups (create, verify, download, delete).
- Backups: database dump, configuration and images in a hashed, optionally encrypted archive,
  verified on creation, with retention; CLI-only restore.
- Security: Argon2id, server-side sessions, CSRF tokens, RBAC (admin, editor, researcher,
  viewer), login lockout, rate limiting, CSP and security headers, secret redaction in logs,
  encrypted credentials.

### Web application
- Next.js App Router with sidebar navigation, global search and command palette (⌘K),
  collection status, notifications, quick create and user menu in the header.
- Light and dark themes with no flash, responsive layouts down to phone width, keyboard
  navigation and visible focus, reduced-motion support.
