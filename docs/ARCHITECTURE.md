# NEWSWIRE — Architecture

NEWSWIRE is a self-hosted, non-AI newsroom intelligence platform. An editor types the
headlines and topics they want to research; NEWSWIRE watches authorised feeds, matches
incoming reports to those assignments with deterministic rules, groups related coverage,
finds openly licensed images, and supports writing and publishing original articles under
human approval.

This document is the "first response" the master prompt asks for: architecture,
technology choices, data model, navigation, design system, API, security, deployment,
milestones and hardware notes. It is kept current as the code changes.

---

## 1. Architecture overview

A **modular monolith**: one backend codebase, two processes, one database.

```
                 ┌──────────────────────────── browser ───────────────────────────┐
                 │  Next.js app (React, TanStack Query)                            │
                 └──────────────┬──────────────────────────────────────────────────┘
                                │ same-origin /api/* (Next.js rewrite or reverse proxy)
┌───────────────────────────────▼───────────────────────────────┐
│  API process  (uvicorn app.main:app)                          │
│  FastAPI routers → services → SQLAlchemy models               │
│  auth/session/CSRF · RBAC · rate limit · audit                │
└───────────────┬───────────────────────────────┬───────────────┘
                │                               │
        ┌───────▼────────┐             ┌────────▼────────────────────────────┐
        │  PostgreSQL     │◄────────────┤  Worker process (python -m app.worker)│
        │  (single source │             │  APScheduler jobs:                   │
        │   of truth)     │             │   collection tick · publishing queue │
        └───────▲────────┘             │   scheduled backups · housekeeping   │
                │                      │   heartbeat → scheduled_jobs table    │
                │                      └────────┬────────────────────────────┘
        ┌───────┴────────┐                      │ bounded, SSRF-guarded HTTP
        │ storage/        │             RSS/Atom/JSON feeds · Openverse ·
        │  images backups │             Wikimedia Commons · WordPress/webhooks
        │  logs           │
        └─────────────────┘
```

* The **worker** owns all scheduled work. The API and the web UI can be stopped without
  stopping ingestion (requirement §33). The API can also run a single source refresh or a
  publishing job inline when a user asks for it explicitly.
* Workers coordinate through the database: due sources are claimed with
  `SELECT … FOR UPDATE SKIP LOCKED` on PostgreSQL, so a second worker never double-fetches.
* Nothing calls an AI model. Matching, geography, deduplication and clustering are rule-based
  and every score carries a human-readable breakdown.

### Backend modules (`backend/app/`)

| Module | Package | Responsibility | Depends on |
|---|---|---|---|
| Source Manager | `services/sources.py`, `api/sources.py` | CRUD, import/export, health, test | collectors |
| News Ingestion Engine | `collectors/` | HTTP fetch (conditional, bounded, SSRF-safe), RSS/Atom/JSON parse, normalisation, persistence | core.http, dedup |
| Feed Scheduler | `scheduler/` | Claim due sources, backoff, job heartbeats, publishing and backup jobs | collectors, publishing, backups |
| Editorial Assignment Manager | `services/assignments.py` | Assignment CRUD, duplicate/pause/archive, keyword sets | matching |
| Keyword Matching Engine | `matching/` | Text normalisation, phrase/token/fuzzy matching, scoring, explanations | geography (entity ancestry) |
| Geographic Classification | `geography/` | Gazetteer, place detection, roles (publisher/event/article/mentioned) | — |
| Deduplication Engine | `collectors/dedup.py`, `core/urls.py` | URL normalisation, canonical/hash/headline duplicates | — |
| Story Clustering | `clustering/` | Group related reports; manual merge/split | matching.text |
| Relevance Scoring | `matching/scoring.py` | Configurable weights from `system_settings` | — |
| Image Discovery & Licensing | `images/` | Openverse + Commons clients, licence normalisation, warnings, download, thumbnails | core.http |
| Research Workspace | `services/research.py` | Aggregates per assignment: matches, clusters, timeline, publishers | matching, clustering |
| Article Editor | `services/articles.py` | Drafts, sanitising, versions, state machine, template, preview | images (licence checks) |
| Publishing Manager | `publishing/` | Destinations (built-in, WordPress, webhook), idempotent job queue, retries | articles |
| Analytics & Reports | `analytics/` | Aggregate SQL queries, CSV export | — |
| Users & Permissions | `security/` | Argon2 hashing, sessions, CSRF, RBAC, rate limits | — |
| Audit & Monitoring | `services/audit.py`, `api/system.py` | Audit trail, health, logs, storage | — |
| Backup & Recovery | `services/backups.py`, `cli.py` | Archive DB + images + config, verify, retention, restore | — |

Routers in `api/` stay thin: validate input (Pydantic), check permission, call a service,
write audit records, return a schema.

## 2. Technology justification

| Choice | Why |
|---|---|
| Python 3.13 + FastAPI + Pydantic v2 | Typed request validation, automatic OpenAPI docs, small footprint. |
| SQLAlchemy 2 (sync) + psycopg 3 | Mature ORM, portable between PostgreSQL and SQLite for tests. Sync code keeps the ingestion path simple; concurrency comes from a bounded thread pool, which is enough for hundreds of feeds. |
| Alembic | Versioned, reviewable migrations. |
| httpx | Timeouts, streaming with size caps, a pluggable transport (used for the SSRF guard), and an in-process mock transport for tests. |
| feedparser | The standard, tolerant RSS/Atom parser. |
| BeautifulSoup | Only to turn feed HTML into text and to read `<link rel=canonical>`/OpenGraph tags when a source explicitly allows page metadata extraction. |
| APScheduler 3 | In-process scheduler, no broker needed. |
| RapidFuzz | Fast C++ fuzzy string matching for headlines and keywords. |
| Pillow | Image verification and thumbnails. |
| argon2-cffi | Argon2id password hashing (OWASP recommendation). |
| nh3 | Maintained HTML sanitiser for article bodies. |
| cryptography (Fernet) | Encrypts stored publishing credentials and, optionally, backups. |
| PostgreSQL | Durable storage, `FOR UPDATE SKIP LOCKED` for job claiming, built-in full-text search (no Elasticsearch). |
| Next.js + React + TypeScript | App Router, file-based routes, same-origin API proxy via rewrites. |
| Tailwind CSS + Radix primitives | Design tokens as CSS variables; accessible dialog/menu/popover/tooltip behaviour without a heavy kit. (Components follow the shadcn/ui pattern: owned source, Radix underneath.) |
| TanStack Query | Caching, polling and background refetch for the live feed without reloading the page. |
| React Hook Form + Zod | Typed, validated multi-section forms. |
| TipTap (ProseMirror) | Real rich-text editing with headings, lists, links, images. |
| Recharts | Charts fed by real API aggregates. |
| next-themes | Light/dark/system with no flash of the wrong theme. |
| Docker Compose | One-command self-hosting (Postgres, API, worker, web, optional Caddy). |

Deliberately **not** used: Redis, Celery, Kafka, Kubernetes, Elasticsearch, any AI/LLM API,
any paid news API. The database is the queue; PostgreSQL FTS is the search engine.

## 3. Database entity-relationship design

UUID primary keys, `created_at`/`updated_at` everywhere, foreign keys with explicit
`ON DELETE` behaviour, soft delete (`deleted_at`) on sources and articles.

```
users ─< user_roles >─ roles
users ─< sessions
users ─< audit_logs, notifications, search_history

sources ─< source_categories >─ categories
sources ─< source_fetch_logs
sources ─< news_items (first-seen source)
sources ─< news_item_sources >─ news_items       (same URL seen in several feeds)

news_items ─┬─ duplicate_of → news_items          (dedup levels 2–4)
            ├─< news_geographies >─ geographic_entities (role: publisher|event|article|mentioned)
            ├─< story_cluster_items >─ story_clusters
            └─< assignment_matches >─ editorial_assignments

geographic_entities ─ parent → geographic_entities (continent → country → region → city)

editorial_assignments ─< assignment_keywords (kind: primary|secondary|synonym|required|excluded)
                      ─< assignment_sources >─ sources
                      ─< assignment_geographies >─ geographic_entities
                      ─ category → categories
                      ─< image_assets (suggested for)

image_assets ─ license → image_licenses
articles ─< article_sources (news item / assignment / external URL references)
         ─< article_assignments >─ editorial_assignments
         ─< article_images >─ image_assets
         ─< article_tags >─ tags
         ─ category → categories, featured_image → image_assets
publishing_destinations ─< publishing_jobs >─ articles   (idempotency_key UNIQUE)
publishing_destinations ─< article_publications >─ articles (remote id/url/state)
system_settings (key → JSON)     scheduled_jobs (name → heartbeat, last run)
backups (archive registry)
```

Key constraints:
* `news_items.normalized_url` UNIQUE — level-1 dedup is enforced by the database.
* `assignment_matches (assignment_id, news_item_id)` UNIQUE — rematching is idempotent.
* `story_cluster_items.news_item_id` UNIQUE — an item belongs to at most one cluster.
* `publishing_jobs.idempotency_key` UNIQUE — a double click cannot publish twice.
* `image_assets (provider, provider_id)` UNIQUE.
* Full-text index on news items: `GIN(to_tsvector('simple', title || ' ' || summary))`.
  `simple` config because coverage is multilingual.

The full column list lives in `backend/app/models/` and the migration in
`backend/migrations/versions/`.

## 4. Application navigation map

```
/login · /setup (first run only)
Sidebar
 ├─ Overview            /
 ├─ Live News           /live
 ├─ My Headlines        /headlines  → /headlines/new · /headlines/[id]/edit
 ├─ Research Workspace  /research   → /research/[assignmentId]
 ├─ Story Clusters      /clusters   → /clusters/[id]
 ├─ Sources             /sources    → /sources/[id] (health)
 ├─ Image Library       /images
 ├─ Articles            /articles   → /articles/[id] (editor) · /articles/[id]/preview
 ├─ Publishing          /publishing
 ├─ Analytics           /analytics
 ├─ Activity Logs       /activity
 └─ Settings            /settings   (profile & theme · scoring · collection · users · backups)
Header: global search (⌘K) · system status → /system · collection status · notifications
        · quick-create · theme toggle · user menu
```

## 5. UI design system

* Tokens are CSS variables in `frontend/app/globals.css` and swapped by the `.dark`
  class: `--bg`, `--surface`, `--surface-2`, `--border`, `--text`, `--muted`, `--accent`,
  `--accent-fg`, `--success`, `--warning`, `--danger`, `--info`, plus chart series colours.
  Tailwind maps them to utilities (`bg-surface`, `text-muted`, …), so every component
  supports both themes by construction.
* Dark: deep navy-charcoal background, slightly lighter surfaces, warm-white text,
  cool-grey muted text, restrained blue accent. Light: soft neutral background, white
  surfaces, deep charcoal text, professional blue.
* Type: Inter (UI) at 13–14 px for dense tables, 15 px body in the editor; a serif
  (Source Serif) only in article preview. Tabular numerals for metrics.
* Radius 6 px on controls, 8 px on cards; 1 px subtle borders; no gradients or glass.
* Status language: green = healthy/published, amber = warning/pending, red = failed/
  blocked, blue = informational/matched, grey = paused/archived. Status is never colour
  alone — every badge has text and an icon.
* Density: compact tables (36 px rows), sticky headers, truncation with full text in a
  tooltip or title attribute.
* Accessibility: focus rings on every control, skip link, semantic landmarks, labelled
  inputs, keyboard shortcuts (`⌘K` search, `g` + key navigation, `⌘S` save in the editor,
  `n` new headline on the headlines page), `prefers-reduced-motion` respected.

## 6. API architecture

* REST under `/api/v1`, documented automatically at `/api/docs` (Swagger) and
  `/api/openapi.json`.
* List endpoints return `{ "items": [...], "total": n, "page": p, "page_size": s }` with
  `page`/`page_size` (max 200) and documented filters/sorts.
* Errors use one shape: `{ "error": { "code": "not_found", "message": "...", "details": {...} } }`
  with conventional status codes (400/401/403/404/409/422/429/500).
* Mutations return the updated resource. Long work (fetching, rematching, publishing) runs
  inline only when explicitly triggered by the user and bounded; otherwise it is queued
  for the worker.
* Optimistic concurrency on articles: `PUT /articles/{id}` carries `version`; a stale
  version returns 409.
* Public read-only endpoints for a custom website: `/api/v1/public/articles[...]`.

## 7. Security model

* **Authentication**: email + password, Argon2id hashes, server-side sessions
  (random 256-bit token; only its SHA-256 is stored), `HttpOnly`, `SameSite=Lax` cookie,
  `Secure` when `NEWSWIRE_COOKIE_SECURE=true`. Idle and absolute expiry. Logout revokes.
* **CSRF**: per-session token returned at login, sent back in `X-CSRF-Token` on every
  unsafe request and compared in constant time.
* **RBAC**: roles `admin`, `editor`, `researcher`, `viewer` mapped to permissions
  (`sources:write`, `assignments:write`, `research:write`, `articles:write`,
  `articles:approve`, `articles:publish`, `images:approve`, `settings:write`,
  `users:manage`, `system:manage`). Checked in a FastAPI dependency per route.
* **Rate limiting**: in-process sliding window per IP (stricter on login).
* **SSRF**: every outbound URL from user input (feeds, API sources, image downloads,
  publishing endpoints) goes through `core/http.py`: http/https only, no embedded
  credentials, DNS resolved and every address checked against private, loopback,
  link-local, multicast, reserved and unspecified ranges; the check is repeated **at
  connect time** inside the transport (defeats DNS rebinding) and on every redirect hop;
  proxies from the environment are ignored; responses are size-capped.
  `NEWSWIRE_ALLOW_PRIVATE_NETWORK=true` exists only for local development.
* **Content**: article HTML is sanitised server-side with nh3; feed HTML is reduced to text.
  React escapes all output; the only `dangerouslySetInnerHTML` is sanitised article HTML.
* **Secrets**: from environment only (`NEWSWIRE_SECRET_KEY`, DB URL). Publishing
  credentials are Fernet-encrypted at rest and never returned by the API. Logs pass through
  a redaction filter (passwords, tokens, Authorization headers, cookies).
* **Uploads**: size limit, Pillow verification against an allow-list of formats, stored
  byte-for-byte (to keep licence metadata) under random names with the detected extension,
  served from `/media` with `nosniff` and a `sandbox` Content-Security-Policy.
* **Audit**: logins, permission changes, source/assignment/article changes, approvals,
  every publishing action, backups and restores.

## 8. Deployment plan

* **Docker Compose** (`docker-compose.yml`): `db` (postgres:17-alpine), `api`, `worker`,
  `web`, optional `caddy` reverse proxy profile. Volumes: `pgdata`, `./storage`.
* **Bare metal / macOS dev**: Homebrew PostgreSQL, a Python venv, Node. `scripts/dev.sh`
  starts API, worker and web; see `README.md`.
* Configuration is environment-only (`.env.example`). Migrations run with
  `alembic upgrade head` (the API container runs it on start).
* Recovery on another machine: `docs/RECOVERY.md`.

## 9. Milestones

| Milestone | Scope | Verified by |
|---|---|---|
| M0 Foundation | repo, docs, compose, DB, backend & frontend skeletons, auth, design system | auth tests, app boots |
| M1 Sources | CRUD, RSS/Atom/JSON parsing, test, scheduler, fetch logs | collector tests with mock transport |
| M2 Ingestion | persistence, URL normalisation, dedup, errors, live feed | dedup + ingestion tests |
| M3 Assignments | headline manager, keywords, sources, geography, matching, explanations | matching/scoring tests |
| M4 Research | clustering, workspace, timeline, publisher comparison | clustering tests |
| M5 Images | Openverse, Commons, licences, attribution, approval | licence tests, mocked provider tests |
| M6 Articles | rich text, references, drafts, preview, state machine | state machine tests |
| M7 Publishing | built-in API, WordPress, webhook, queue, retries, audit | publishing tests with mocked endpoints |
| M8 Analytics & reliability | reports, monitoring, backups, security review | backup/restore test |
| M9 Hardening | end-to-end test, docs, deployment and recovery guides | full suite + browser walkthrough |

Progress is tracked in `docs/CHANGELOG.md` and `docs/KNOWN_ISSUES.md`.

## 10. Hardware and performance

Target: a laptop or small server with 2 CPU cores and 4–8 GB RAM.

* Idle footprint: API ~80 MB, worker ~90 MB, Next.js production server ~120 MB,
  PostgreSQL ~100 MB.
* Ingestion uses a thread pool capped by `NEWSWIRE_FETCH_CONCURRENCY` (default 4) and a
  per-host politeness delay; conditional requests make unchanged feeds almost free.
* The collection tick runs every 30 s but only fetches sources whose `next_fetch_at` has
  passed (minimum interval 60 s, exponential backoff on failure).
* Clustering and dedup compare a new item only against items from a bounded time window
  (default 48 h) using RapidFuzz batch scoring.
* Matching only scores new items (plus explicit rematch/backfill windows).
* Every list is paginated and filtered on indexed columns; the live feed polls a cheap
  "since" query and prepends new rows without reloading the page.
* Images: thumbnails generated once; lists lazy-load images.
