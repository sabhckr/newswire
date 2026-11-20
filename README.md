# NEWSWIRE

A self-hosted newsroom intelligence and publishing platform that runs without AI and without
paid services.

You tell NEWSWIRE which headlines and topics you are researching. It watches the RSS, Atom and
JSON feeds and news sitemaps you authorise, matches each incoming report to your assignments with transparent
rules, groups related coverage, flags duplicates, works out which places a story is about, and
finds openly licensed images. You write original articles in the built-in editor. They move
through review and approval, and you publish them yourself to the built-in public API, a
WordPress site or any website that accepts a signed webhook.

* **Deterministic.** Matching, deduplication, clustering and geography are rules you can read.
  Every score shows which keywords and signals earned which points.
* **Zero recurring cost.** PostgreSQL, Python, Node.js and free public image APIs
  (Openverse, Wikimedia Commons). No AI models, no paid news APIs, no cloud account.
* **Human in control.** Nothing publishes automatically unless an administrator turns on
  auto-publishing, and even then images with unknown, non-commercial or no-derivatives
  licences block it.
* **Polite collection.** NEWSWIRE honours robots.txt, uses conditional requests, backs off
  failing feeds and never bypasses paywalls or logins.
* **Built for Bangladeshi news.** Bangla and English matching that understands Bangla
  inflections (`ঢাকা` finds `ঢাকায়`), every division and district of Bangladesh with Bangla
  names, Bangla datelines and correspondent credits, news-sitemap support for papers without
  RSS, and a source pack of Bangladeshi newspapers' public feeds. It works for news from
  anywhere; Bangladesh simply has the deepest coverage.

## Contents

| Path | What it holds |
|---|---|
| `backend/` | FastAPI API, collection worker, CLI, Alembic migrations, tests |
| `frontend/` | Next.js web application |
| `infrastructure/` | Caddy and Nginx reverse-proxy configs, systemd units |
| `scripts/` | `dev.sh` (local run), `gen_types.py` (TypeScript types from the API schemas) |
| `storage/` | Runtime data: images, backups, logs, the development secret key (not in git) |
| `docs/` | Architecture, deployment, recovery, security, publishing, matching, geography, user guide |
| `docker-compose.yml` | PostgreSQL, API, worker, web and an optional Caddy HTTPS proxy |

## Quick start with Docker

Requires Docker with the Compose plugin.

```bash
cp .env.example .env
```

Edit `.env` and set `NEWSWIRE_SECRET_KEY` (64+ random characters) and `POSTGRES_PASSWORD`.

```bash
docker compose up -d --build
```

Open <http://localhost:3000>. The first visit asks you to create the administrator account.

For HTTPS on a public domain, point the domain's DNS at the server, set `NEWSWIRE_DOMAIN`,
`NEWSWIRE_PUBLIC_BASE_URL=https://your.domain` and `NEWSWIRE_COOKIE_SECURE=true` in `.env`, then:

```bash
docker compose --profile proxy up -d
```

Caddy obtains a free Let's Encrypt certificate. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Local development (macOS or Linux, no Docker)

Requirements: Python 3.13 (the tested version), Node.js 20+, PostgreSQL 15+ (SQLite works for tests).

```bash
createdb newswire
```

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.lock
```

```bash
cd frontend && npm ci
```

```bash
./scripts/dev.sh
```

`dev.sh` runs the migrations, then starts the API on port 8000, the worker and the web UI on
port 3000. In development the secret key is generated once into `storage/.secret_key`.

Useful commands (run in `backend/`):

```bash
.venv/bin/python -m pytest -q
```

```bash
.venv/bin/python -m app.cli --help
```

The CLI covers `migrate`, `bootstrap`, `create-admin`, `reset-password`, `backup`,
`verify-backup`, `restore`, `import-geonames` and `robot-check`.

Publishing through a website's own admin panel uses NEWSWIRE's robot browser engine
(`backend/app/robot`, pure Python). It drives the Google Chrome, Microsoft Edge, Brave or
Chromium already installed on the computer; `robot-check` shows which one and that it works.

After changing API schemas, regenerate the frontend types from the repository root:

```bash
NEWSWIRE_SECRET_KEY=dev backend/.venv/bin/python scripts/gen_types.py
```

### Browser tests

A [Playwright](https://playwright.dev) suite in `frontend/e2e/` drives headless Chromium through
the main flows: first-run `/setup`, adding a source in the Sources dialog, a headline with its
live preview, verifying a match and starting an article in the Research workspace, editing and
saving with ⌘S, approving, publishing to the built-in public API and unpublishing. It also checks
that no sidebar page scrolls sideways at 375 px and that the theme toggle works.

It needs the backend virtualenv and `npm ci` from above, plus a one-time browser download
(about 90 MB, kept in Playwright's cache outside the repository):

```bash
cd frontend && npx playwright install --only-shell chromium
```

Run it from `frontend/`:

```bash
npm run test:e2e
```

The suite starts its own servers on 127.0.0.1 and stops them when it finishes, so a development
server on ports 3000 and 8000 can keep running. PostgreSQL is not needed.

| Port | Server |
|---|---|
| 8766 | A fixture RSS feed (`e2e/harness/fixture-feed.mjs`). No external site is contacted. |
| 8765 | The API on a throwaway SQLite database in `$TMPDIR/newswire-e2e`, wiped at the start of every run. `NEWSWIRE_ALLOW_PRIVATE_NETWORK=true` is set for this process only, so it can fetch the fixture feed. |
| 3765 | A production build of the web app in `frontend/.next-e2e` (the dev server's `.next` is left alone). |

Each run rebuilds the web app first, which takes a minute or two. Options, as environment
variables:

* `E2E_SKIP_BUILD=1` reuses the last e2e build. Faster while writing tests, but it runs stale
  code if the app changed since.
* `E2E_WEB_PORT`, `E2E_API_PORT`, `E2E_FEED_PORT` move a server to another port.
* `E2E_SERVER_LOGS=1` shows the servers' output.
* `NEWSWIRE_E2E_PYTHON` points at a Python other than `backend/.venv/bin/python`.

`npm run test:e2e:report` opens the HTML report. A failing test keeps its trace and screenshot in
`frontend/test-results/`; open a trace with `npx playwright show-trace <path>`.

## First steps after installing

1. **Sources.** Add the feeds you are allowed to monitor: *Source packs* adds the bundled
   Bangladeshi newspapers in one step, and *Add source → Find feeds* finds the feeds and news
   sitemaps of any other site. Use *Test* before saving to see what a feed returns. The worker
   polls each source on its own interval.
2. **My Headlines.** Create an assignment: the headline you are researching, its primary
   keywords, synonyms, required and excluded phrases, and optional countries, sources,
   category and language.
3. **Live News and Research.** Matching reports appear with their score and an explanation.
   Mark each match verified or rejected; rejected matches teach you which keywords to tighten.
4. **Image Library.** Search Openverse and Wikimedia Commons, check the licence warnings, save
   and approve images.
5. **Articles.** Start a draft from a research workspace, write it, attach references and an
   approved image, then submit it for review.
6. **Publishing.** Approve the article and publish it to the built-in public API
   (`/api/v1/public/articles`, `/api/v1/public/feed.xml`), WordPress or a webhook.

The [user guide](docs/USER_GUIDE.md) walks through every page.

## Documentation

* [Architecture](docs/ARCHITECTURE.md): modules, data model, API, security model, milestones
* [Deployment](docs/DEPLOYMENT.md): Docker, bare metal, reverse proxies, upgrades, troubleshooting
* [Backup and recovery](docs/RECOVERY.md): restoring on a new machine
* [Security](docs/SECURITY.md): threat model and hardening checklist
* [Matching and scoring](docs/MATCHING.md): how relevance, dedup and clustering decide
* [Geography](docs/GEOGRAPHY.md): the gazetteer, location roles and GeoNames import
* [Publishing](docs/PUBLISHING.md): destinations, the public API and webhook signatures
* [User guide](docs/USER_GUIDE.md)
* [Changelog](docs/CHANGELOG.md) and [known issues](docs/KNOWN_ISSUES.md)

The live API reference is served at `/api/docs` (Swagger UI) and `/api/openapi.json`.

## Licence and data

NEWSWIRE stores headlines, summaries and links from the feeds you configure. The full text of a
story is read only when an editor asks for it (**Load full article**), and robots.txt, logins and
paywalls are respected. News text and photos belong to their publishers: rewrite and credit
before you publish, and respect each publisher's terms. Image licences and attribution come from the
providers. Check them before publishing. Optional GeoNames data is licensed CC BY 4.0.
