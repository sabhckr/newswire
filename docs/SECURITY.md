# Security

NEWSWIRE fetches URLs that users type, stores publishing credentials and can push content to
public websites. This page lists what it defends against, how, and what you need to do as the
operator. The design summary is in [ARCHITECTURE.md §7](ARCHITECTURE.md#7-security-model).

## Accounts and sessions

* Passwords are hashed with **Argon2id** (argon2-cffi defaults) and rehashed automatically if the
  parameters change. The minimum length is 10 characters.
* Signing in creates a server-side session. The browser holds a random 256-bit token in the
  `nw_session` cookie (`HttpOnly`, `SameSite=Lax`, `Secure` when `NEWSWIRE_COOKIE_SECURE=true`);
  the database stores only its SHA-256. Sessions expire after `NEWSWIRE_SESSION_IDLE_MINUTES` of
  inactivity (default 12 h) and `NEWSWIRE_SESSION_ABSOLUTE_HOURS` in total (default 14 days).
* **CSRF:** every state-changing request must send the session's CSRF token in `X-CSRF-Token`;
  it is compared in constant time. The token is also in the readable `nw_csrf` cookie so the web
  UI can recover it after a reload.
* **Brute force:** after 8 failed attempts an account is locked for 15 minutes, and sign-in is
  rate-limited per IP (`NEWSWIRE_LOGIN_RATE_LIMIT_PER_MINUTE`, default 10). Failed and
  successful sign-ins are written to the audit log.
* Changing a password signs out the user's other sessions. `reset-password` on the CLI signs out
  all of them. Deactivating a user ends their sessions immediately.

## Roles

| Role | Can |
|---|---|
| `admin` | everything, including users, settings, publishing destinations, system and backups |
| `editor` | sources, assignments, research, clusters, images (including approval), articles; approve and publish |
| `researcher` | assignments, match review, clusters, image collection, drafting articles |
| `viewer` | read-only |

Permissions are checked on the server for every route. The UI hides what a role cannot do, but
it is not the gate.

## Outbound requests (SSRF)

Feed URLs, image downloads, WordPress sites and webhook URLs all come from users. Every one goes
through `app/core/http.py`:

* http and https only. URLs with embedded credentials are refused.
* The host is resolved and **every** address is checked against private, loopback, link-local,
  carrier-grade NAT, multicast, reserved and unspecified ranges (IPv4 and IPv6, including
  IPv4-mapped addresses).
* The check runs again **at connect time** inside the transport, which defeats DNS rebinding,
  and again on every redirect hop (at most 5).
* Proxy variables from the environment are ignored (`trust_env=False`).
* Response bodies are streamed with a hard size cap: feeds 5 MB, images 15 MB, publishing
  responses 1–5 MB.

`NEWSWIRE_ALLOW_PRIVATE_NETWORK=true` turns the address check off. Use it only on a development
machine.

## Collection etiquette

* robots.txt is fetched and cached per host. A disallowed feed is recorded as *Blocked* and never
  fetched.
* Requests carry `NEWSWIRE_USER_AGENT` (plus `NEWSWIRE_CONTACT` if set), use `If-None-Match` /
  `If-Modified-Since`, honour `Retry-After`, wait `NEWSWIRE_PER_HOST_DELAY_SECONDS` between
  requests to the same host and back off exponentially on failure.
* NEWSWIRE never sends cookies or credentials to feeds and never tries to get past paywalls or
  logins. Full-page fetching is off by default per source. When enabled, it reads only the
  canonical link and OpenGraph metadata.

## Content

* Article bodies are sanitised server-side with **nh3** (an allow-list of tags, attributes and
  URL schemes) on every save. Feed HTML is reduced to plain text before storage.
* React escapes all output. The only raw HTML rendered is sanitised article HTML in the preview,
  which is served with its own restrictive Content-Security-Policy.
* API responses carry `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`,
  `X-Content-Type-Options: nosniff` and a strict `Referrer-Policy`. `/media` files are served with
  `sandbox` CSP so an uploaded file can never run script on the app's origin.
* Uploads and downloaded images are size-limited and must pass Pillow's verification as a JPEG,
  PNG, GIF or WebP file (decompression bombs are rejected). They are stored under random names with
  an extension taken from the detected format, never the user's filename. The original bytes are
  kept so embedded licence and creator metadata survive; the `nosniff` and `sandbox` headers on
  `/media` stop a crafted file from being run as anything but an image.

## Secrets

* Configuration comes only from the environment or `.env`. Nothing secret is sent to the
  frontend. The browser never sees WordPress passwords, webhook secrets or API keys.
* Publishing credentials are encrypted with a key derived from `NEWSWIRE_SECRET_KEY` (Fernet).
  The API reports only whether a secret is set.
* Logs pass through a redaction filter (values of password, token, secret, cookie and API-key
  fields, and Basic/Bearer Authorization values). Exported logs are redacted again. The database
  URL is never logged.
* Backups can be encrypted with `NEWSWIRE_BACKUP_ENCRYPTION_KEY`. They are written `0600`.

## Audit trail

Every sign-in, user and role change, source, assignment, article and image change, approval,
publishing action, setting change, backup and collection run is recorded with the actor, IP
address and a readable summary. The trail has no edit or delete endpoint. It is under
*Activity Logs* and can be exported as CSV.

## Operator checklist

- [ ] Set a long random `NEWSWIRE_SECRET_KEY`, keep a copy offline, and set `NEWSWIRE_ENV=production`.
- [ ] Serve over HTTPS and set `NEWSWIRE_COOKIE_SECURE=true`.
- [ ] Do not expose PostgreSQL or the API port (8000) to the internet. Only the reverse proxy.
- [ ] Leave `NEWSWIRE_ALLOW_PRIVATE_NETWORK=false`.
- [ ] Give people the lowest role that lets them work. Keep the number of admins small.
- [ ] Copy backups off the machine and run a restore drill ([RECOVERY.md](RECOVERY.md)).
- [ ] Keep the OS, PostgreSQL, Python and Node.js patched. Rebuild containers regularly.
- [ ] Review *Activity Logs* for unexpected sign-ins or publishing.

## Reporting a problem

This is a self-hosted project with no central service. Report vulnerabilities to whoever
maintains your installation, and fix them in your copy of the code.
