# Known issues and limitations

Honest list of what is untested, incomplete or limited by design in 0.9.0. Each entry says what
to do about it.

## Untested

| Area | Status | Workaround / next step |
|---|---|---|
| Docker images and `docker-compose.yml` | Written but **never built**: the development machine has no Docker. The bare-metal path (PostgreSQL 17, Python 3.13, Node) is what has been run and tested. | Run `docker compose up --build` once on a machine with Docker and fix anything that fails before relying on it. |
| Caddy, Nginx and systemd configs | Not exercised. | Treat as templates. |
| Pages outside the main browser flows | The Playwright suite (`frontend/e2e/`, Chromium only, SQLite) covers setup, sources, headlines, research, the article editor, publishing to the built-in API and unpublishing, plus a 375 px overflow check of every sidebar page and theme switching. The Image Library, WordPress and webhook destinations, users and roles, source import, scheduled publishing and the worker's own polling are covered by API tests only. | Extend `frontend/e2e/` when changing those pages. |
| WordPress connector | Tested against a mocked REST API, not a live WordPress site. | Use *Test connection* and *Publish as draft* first. |
| Openverse / Wikimedia Commons | Parsers tested on recorded response shapes; live calls depend on those services. | Search returns a clear provider error when they are unavailable. |

## Limitations by design

* **Lexical matching only.** NEWSWIRE matches words and phrases, not meaning. Synonyms must be
  entered by hand. Stemming is light, English-oriented plural stripping, and the stop-word list
  used by clustering is English. Other languages match on exact phrases but cluster less well.
* **Full-text search** uses PostgreSQL's `simple` configuration (no stemming), so it works for
  any language but `strike` does not find `strikes`. On SQLite it falls back to substring search.
* **Built-in gazetteer** covers countries and about 460 major cities. Regions, districts and
  small towns need the optional GeoNames import ([GEOGRAPHY.md](GEOGRAPHY.md)). Place names
  shared by several places get low confidence unless the report also names the country.
* **Rate limiting** is per API process, in memory. With several API replicas, each counts on
  its own. Put a reverse-proxy limit in front for multi-replica deployments.
* **Single worker on SQLite.** SQLite has no `SKIP LOCKED`. Use PostgreSQL if you run more than
  one worker.
* **WordPress mapping** sends title, content, excerpt, slug and featured image (with caption and
  credit). NEWSWIRE categories and tags are not mapped to WordPress taxonomies.
* **Webhook unpublish** sends `article.unpublished`; removing the article is up to the
  receiving site.
* **Analytics** reports use UTC days. The Overview page uses the browser's time zone.
* **The web UI's API address is fixed at build time** (`NEWSWIRE_BACKEND_URL`, Next.js
  rewrites). Rebuild the web image if the API moves.
* **Restore** is CLI-only and expects you to stop the API and worker first.

## Not built

* Email delivery of notifications (in-app only).
* Single sign-on (SAML/OIDC) and two-factor authentication.
* Paywalled or login-protected sources. By policy, these will not be added.
