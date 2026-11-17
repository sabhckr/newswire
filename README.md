# NEWSWIRE

A self-hosted newsroom intelligence and publishing platform that runs without AI and without
paid services.

You tell NEWSWIRE which headlines and topics you are researching. It watches the RSS, Atom and
JSON feeds you authorise, matches each incoming report to your assignments with transparent
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
`verify-backup`, `restore` and `import-geonames`.

After changing API schemas, regenerate the frontend types from the repository root:

```bash
NEWSWIRE_SECRET_KEY=dev backend/.venv/bin/python scripts/gen_types.py
```

## First steps after installing

1. **Sources.** Add the RSS/Atom/JSON feeds you are allowed to monitor. Use *Test* before
   saving to see what the feed returns. The worker polls each source on its own interval.
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

NEWSWIRE stores headlines, summaries and links from the feeds you configure. It does not
copy full articles unless a source's terms allow it and you enable page metadata extraction
for that source. Respect each publisher's terms. Image licences and attribution come from the
providers. Check them before publishing. Optional GeoNames data is licensed CC BY 4.0.
