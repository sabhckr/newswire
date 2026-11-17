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
