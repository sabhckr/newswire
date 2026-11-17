# Backup and recovery

## What a backup contains

Each backup is one `tar.gz` archive in `storage/backups/` (`/data/backups` in Docker). With
`NEWSWIRE_BACKUP_ENCRYPTION_KEY` set, it is Fernet-encrypted as `.tar.gz.enc`.

| Member | Contents |
|---|---|
| `manifest.json` | NEWSWIRE version, database kind, Alembic revision, SHA-256 of every member |
| `database.dump` | `pg_dump --format=custom` of the whole database (PostgreSQL) |
| `database.sqlite` | a consistent copy made with SQLite's backup API (SQLite installs) |
| `config.json` | sources, assignments, settings, categories, destinations, readable without a database (no secrets) |
| `images/…` | downloaded and uploaded images (optional; on by default). **Uploaded videos (`images/videos/`) are left out**: they can be gigabytes each |

The database dump holds everything else: users, sessions, news items, matches, clusters,
articles and their versions, publishing history and the audit log. Publishing credentials are
stored encrypted with `NEWSWIRE_SECRET_KEY`. **Keep a copy of that key (and the backup
encryption key, if you use one) somewhere other than the server.** Without the secret key, a
restore still works, but you must re-enter WordPress passwords and webhook secrets.

Back up `storage/images/videos/` separately (for example with `rsync`) if you need the videos
after a restore. Without them, articles keep their video entry; publishing one again says the
file is missing and asks you to upload it again. Posts already on Facebook, YouTube or WordPress
are not affected.

## Making backups

* **Scheduled:** the worker creates one every `interval_hours` (default 24) and keeps the newest
  `retention_count` (default 7). Change both under *Settings → Collection & backups*.
* **On demand:** *System → Backups → Create backup*, or `python -m app.cli backup [--no-images]`.
* **Verify:** each backup is verified when it is created. Re-check any archive at any time:
  *System → Backups → Verify*, or `python -m app.cli verify-backup PATH`. Verification
  re-hashes every member against the manifest and checks that the dump is readable
  (`pg_restore --list` or `PRAGMA integrity_check`).
* **Copy off the machine.** A backup on the same disk does not survive the disk. Download it from
  the System page or copy `storage/backups/` elsewhere, for example with a nightly `rsync`.

## Restoring

Restoring overwrites the target database and image files. It is deliberately CLI-only and
asks for `--yes`.

1. Stop the worker and the API so nothing writes during the restore.
   `docker compose stop api worker web` or `systemctl stop newswire-worker newswire-api`.
2. Make sure the same `NEWSWIRE_SECRET_KEY` (and `NEWSWIRE_BACKUP_ENCRYPTION_KEY` for `.enc`
   archives) is in the environment.
3. Verify the archive:

   ```bash
   python -m app.cli verify-backup /path/to/newswire-20261117T020000Z-a1b2c3.tar.gz
   ```

4. Restore into the configured database:

   ```bash
   python -m app.cli restore /path/to/newswire-20261117T020000Z-a1b2c3.tar.gz --yes
   ```

   Add `--database-url postgresql+psycopg://…/other_db` to restore somewhere else, for example a
   scratch database to inspect an old state. Add `--no-images` to leave image files alone.
5. If the backup came from an older version, run `python -m app.cli migrate`.
6. Start the API and worker again and open *System* to confirm the migration, worker and counts.

PostgreSQL restores use `pg_restore --clean --if-exists --no-owner --no-privileges`, so the
target database must exist but may be empty or hold an older copy.

## Recovering on a new machine

1. Install NEWSWIRE (see [DEPLOYMENT.md](DEPLOYMENT.md)) but do not create an administrator.
2. Create the empty database (`createdb newswire`, or start only the `db` service with
   `docker compose up -d db`).
3. Copy the backup archive and your `.env` (with the original `NEWSWIRE_SECRET_KEY`) across.
4. Restore as above. In Docker:

   ```bash
   docker compose run --rm -v "$PWD/newswire-backup.tar.gz:/restore.tar.gz:ro" api python -m app.cli restore /restore.tar.gz --yes
   ```

5. Start everything and sign in with the accounts from the old machine. Sessions from the old
   machine are restored too. Changing your password (*Settings → Profile & theme*) signs out every
   other session, and `python -m app.cli reset-password EMAIL` does the same for any user.

## Recovery drills

The restore path has been exercised on PostgreSQL 17: backup, verify, then restore into a
scratch database with matching row counts. The automated suite runs the SQLite round trip
(`tests/test_rules.py::test_backup_round_trip_restores_data`). Run a drill after major upgrades: restore
the latest backup into a scratch database with `--database-url` and check it.
