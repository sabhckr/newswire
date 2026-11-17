# Deployment

NEWSWIRE runs as four processes: PostgreSQL, the API (FastAPI/uvicorn), the worker
(scheduled collection, publishing queue, backups) and the web UI (Next.js). They fit on a
2-core, 4 GB machine. Everything is configured through environment variables. The full list
with comments is in [`.env.example`](../.env.example).

> **Status:** the Docker files have not yet been built on the development machine, which has no
> Docker. The bare-metal path below is the one that has been run end to end. See
> [KNOWN_ISSUES.md](KNOWN_ISSUES.md).

## Option A: Docker Compose

```bash
cp .env.example .env
```

Set at least:

| Variable | Value |
|---|---|
| `NEWSWIRE_SECRET_KEY` | 64+ random characters: `python3 -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `POSTGRES_PASSWORD` | a strong password for the bundled database |
| `NEWSWIRE_PUBLIC_BASE_URL` | the address people use, e.g. `https://news.example.org` |

```bash
docker compose up -d --build
```

* `db`: PostgreSQL 17 with data in the `newswire-db` volume.
* `api`: runs `python -m app.cli migrate`, then uvicorn on port 8000 (internal only).
* `worker`: `python -m app.worker`.
* `web`: Next.js on `${NEWSWIRE_HTTP_PORT:-3000}`. It proxies `/api/*` and `/media/*` to the API.
* Images, backups and logs live in the `newswire-data` volume, mounted at `/data`.

Open `http://SERVER:3000` and create the administrator.

### HTTPS with Caddy

Point the domain's DNS at the server and open ports 80 and 443. Then add to `.env`:

```
NEWSWIRE_DOMAIN=news.example.org
NEWSWIRE_PUBLIC_BASE_URL=https://news.example.org
NEWSWIRE_COOKIE_SECURE=true
```

```bash
docker compose --profile proxy up -d
```

Caddy fetches and renews a Let's Encrypt certificate. Once it works, stop publishing port 3000
(set `NEWSWIRE_HTTP_PORT=127.0.0.1:3000`) so that all traffic goes through HTTPS.

### Useful commands

```bash
docker compose logs -f worker
```

```bash
docker compose exec api python -m app.cli backup
```

```bash
docker compose exec api python -m app.cli reset-password editor@example.org
```

## Option B: bare metal (Debian/Ubuntu or macOS)

1. Install PostgreSQL 15+ (with matching client tools for `pg_dump`), Python 3.13 and Node.js 20+.
2. Create a database and user:

   ```bash
   sudo -u postgres createuser newswire --pwprompt
   ```

   ```bash
   sudo -u postgres createdb newswire --owner newswire
   ```

3. Put the code in `/opt/newswire` and create `/opt/newswire/.env` from `.env.example`, with
   `NEWSWIRE_DATABASE_URL=postgresql+psycopg://newswire:PASSWORD@localhost:5432/newswire` and
   `NEWSWIRE_STORAGE_DIR=/var/lib/newswire`.
4. Backend:

   ```bash
   cd /opt/newswire/backend && python3.13 -m venv .venv && .venv/bin/pip install -r requirements.lock
   ```

   ```bash
   .venv/bin/python -m app.cli migrate
   ```

5. Frontend (standalone build). The standalone server does not serve static files on its own,
   so copy them in:

   ```bash
   cd /opt/newswire/frontend && npm ci && NEWSWIRE_BACKEND_URL=http://127.0.0.1:8000 npm run build
   ```

   ```bash
   cp -r .next/static .next/standalone/.next/ && cp -r public .next/standalone/
   ```

6. Install the units from `infrastructure/systemd/` into `/etc/systemd/system/`, adjust
   paths, then `systemctl enable --now newswire-api newswire-worker newswire-web`.
7. Put a reverse proxy in front: `infrastructure/caddy/Caddyfile` (replace `api:8000` and
   `web:3000` with `127.0.0.1:8000` / `127.0.0.1:3000`) or `infrastructure/nginx/newswire.conf`.
   Route `/api/*` and `/media/*` to the API and everything else to the web UI.

`scripts/dev.sh` is the same layout for a laptop: it runs migrations and starts the three
processes in the foreground.

## Processes and scheduling

* The **worker** owns scheduled work: a collection tick every `NEWSWIRE_SCHEDULER_TICK_SECONDS`
  (30 s) that fetches only sources whose next fetch time has passed, the publishing queue,
  scheduled backups and housekeeping. It writes a heartbeat that the System page shows.
* You can stop the API and web UI without stopping collection. If the worker stops, the header's
  collection indicator turns red and the System page reports the worker as stale or not running.
* On PostgreSQL, sources and jobs are claimed with `FOR UPDATE SKIP LOCKED`. Running two
  workers is safe but rarely needed.
* `NEWSWIRE_EMBEDDED_SCHEDULER=true` runs the jobs inside the API process instead. It is
  meant for single-process demos. Do not combine it with a separate worker.

## Upgrading

1. Take a backup: `python -m app.cli backup` (or *System → Backups → Create backup*).
2. Update the code and dependencies. Rebuild the containers with `docker compose up -d --build`.
3. Migrations run automatically when the API starts (`python -m app.cli migrate`). Migrations
   that would drop or rewrite data are called out in the [changelog](CHANGELOG.md). Read it before
   upgrading.

## Health and monitoring

* `GET /api/v1/health` needs no authentication. It returns `200 {"status": "ok", "database": "ok", "version": …}` when the
  database answers.
* *System* (admin only) shows the version, database and migration, worker heartbeat, job
  schedule, failing sources, storage use, the last backup and recent logs, which you can export
  (redacted).
* Logs are JSON lines on stdout (set `NEWSWIRE_LOG_JSON=false` for plain text). Secrets, cookies
  and Authorization headers are redacted before they are written.

## Troubleshooting

| Symptom | Check |
|---|---|
| `NEWSWIRE_SECRET_KEY must be set in production` | Set it in `.env`. In development a key is generated in `storage/.secret_key`. |
| API exits with "Run `alembic upgrade head`" | The database has no schema yet: `python -m app.cli migrate`. |
| Worker shows "stale" or "not running" | Its heartbeat is older than 2 minutes, or it never started. Check `docker compose logs worker` or `journalctl -u newswire-worker`. |
| A source says *Blocked by robots.txt* | The publisher disallows automated access to that feed. NEWSWIRE will not fetch it. Ask the publisher or remove the source. |
| A feed on your LAN fails with "private network address" | The SSRF guard blocks private addresses. For development only, set `NEWSWIRE_ALLOW_PRIVATE_NETWORK=true`. |
| Image search returns nothing | Openverse limits anonymous clients. Add free API credentials (`NEWSWIRE_OPENVERSE_CLIENT_ID/SECRET`) or use Commons. |
| `pg_dump: server version mismatch` | Install client tools of the same major version as the server, or set `NEWSWIRE_PG_DUMP_PATH`. |
| Signed out repeatedly over plain HTTP | `NEWSWIRE_COOKIE_SECURE=true` requires HTTPS. Set it to false for HTTP-only LAN use. |
| Locked out of every admin account | `python -m app.cli reset-password you@example.org` or `python -m app.cli create-admin`. |
