"""Administrative command line: `python -m app.cli <command>`.

Commands
  migrate                      Apply database migrations (alembic upgrade head)
  bootstrap                    Seed roles, categories, job definitions and the built-in gazetteer
  create-admin                 Create an administrator (prompts for the password)
  reset-password EMAIL         Set a new password for a user and revoke their sessions
  backup [--no-images]         Create a verified backup archive in storage/backups
  verify-backup PATH           Check a backup archive's checksums and database dump
  restore PATH --yes           Restore a backup (DESTRUCTIVE: replaces the target database)
  import-geonames ADMIN1 CITIES [--min-population N]
  import-sources (--pack NAME | FILE)  Add sources from a bundled pack (e.g. bangladesh) or a sources JSON file
  list-source-packs            List the bundled source packs
                               Import GeoNames admin1CodesASCII.txt and a cities file (CC BY 4.0)
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from sqlalchemy import select


def _db():
    from app.core.db import SessionLocal

    return SessionLocal()


def cmd_migrate(_args) -> int:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(cfg, "head")
    print("Database is at the latest migration.")
    return 0


def cmd_bootstrap(_args) -> int:
    from app.services.bootstrap import bootstrap

    db = _db()
    try:
        bootstrap(db)
        db.commit()
    finally:
        db.close()
    print("Bootstrap complete.")
    return 0


def _read_password(confirm: bool = True) -> str:
    from app.security.passwords import password_problems

    while True:
        pw = getpass.getpass("Password: ")
        problems = password_problems(pw)
        if problems:
            print("; ".join(problems))
            continue
        if confirm and getpass.getpass("Repeat password: ") != pw:
            print("Passwords do not match.")
            continue
        return pw


def cmd_create_admin(args) -> int:
    from app.models import Role, User
    from app.security.passwords import hash_password
    from app.services.audit import record
    from app.services.bootstrap import bootstrap

    db = _db()
    try:
        bootstrap(db)
        email = (args.email or input("Email: ")).strip().lower()
        if db.scalar(select(User).where(User.email == email)):
            print(f"A user with email {email} already exists.", file=sys.stderr)
            return 1
        name = args.name or input("Display name: ").strip() or email
        pw = _read_password()
        role = db.scalar(select(Role).where(Role.name == "admin"))
        user = User(email=email, display_name=name, password_hash=hash_password(pw), roles=[role])
        db.add(user)
        db.flush()
        record(db, "user.created", f"Administrator {email} created from the command line", entity_type="user", entity_id=user.id)
        db.commit()
        print(f"Administrator {email} created.")
        return 0
    finally:
        db.close()


def cmd_reset_password(args) -> int:
    from app.models import User
    from app.security.passwords import hash_password
    from app.security.sessions import revoke_all_for_user
    from app.services.audit import record

    db = _db()
    try:
        user = db.scalar(select(User).where(User.email == args.email.strip().lower()))
        if user is None:
            print("No such user.", file=sys.stderr)
            return 1
        user.password_hash = hash_password(_read_password())
        user.failed_login_count = 0
        user.locked_until = None
        revoke_all_for_user(db, user.id)
        record(db, "user.password_reset", f"Password for {user.email} reset from the command line", entity_type="user", entity_id=user.id)
        db.commit()
        print("Password updated; existing sessions were signed out.")
        return 0
    finally:
        db.close()


def cmd_backup(args) -> int:
    from app.core.config import get_settings
    from app.services.backups import create_backup

    db = _db()
    try:
        b = create_backup(db, kind="manual", include_images=not args.no_images)
        if b.status != "completed":
            print(f"Backup failed: {b.error}", file=sys.stderr)
            return 1
        print(f"{get_settings().backups_dir / b.filename}\n{b.size_bytes} bytes, sha256 {b.sha256}\n{b.verify_status}")
        return 0
    finally:
        db.close()


def cmd_verify(args) -> int:
    from app.services.backups import verify_file

    ok, message = verify_file(Path(args.path))
    print(("OK: " if ok else "FAILED: ") + message)
    return 0 if ok else 1


def cmd_restore(args) -> int:
    from app.core.config import get_settings
    from app.services.backups import BackupError, restore

    target = args.database_url or get_settings().database_url
    if not args.yes:
        print("Restoring replaces the contents of the target database and image files.\n"
              f"Target: {target.split('@')[-1]}\nRe-run with --yes to confirm.", file=sys.stderr)
        return 2
    try:
        result = restore(Path(args.path), database_url=target, restore_images=not args.no_images)
    except BackupError as exc:
        print(f"Restore failed: {exc}", file=sys.stderr)
        return 1
    print(f"Restored {result['database']} database (migration {result['alembic_revision']}) and {result['images']} image files.\n"
          "Run `python -m app.cli migrate` if this backup came from an older version, then restart the API and worker.")
    return 0


def cmd_list_source_packs(_args) -> int:
    from app.services.source_import import list_packs

    for pack in list_packs():
        print(f"{pack['id']:<16} {pack['count']:>4} sources  {pack['name']}  (verified {pack['verified_at']})")
    return 0


def cmd_import_sources(args) -> int:
    import json

    from app.services.audit import record
    from app.services.bootstrap import bootstrap
    from app.services.source_import import import_entries, load_pack

    if args.pack:
        entries = load_pack(args.pack)
        if entries is None:
            print(f"No source pack named {args.pack!r}. Run list-source-packs.", file=sys.stderr)
            return 1
    elif args.file:
        data = json.loads(Path(args.file).read_text(encoding="utf-8"))
        entries = data.get("sources", []) if isinstance(data, dict) else data
    else:
        print("Give --pack NAME or a FILE", file=sys.stderr)
        return 2
    db = _db()
    try:
        bootstrap(db)
        result = import_entries(db, entries)
        record(db, "source.imported", f"CLI import: {result['created']} sources added, {result['skipped']} already present",
               entity_type="source")
        db.commit()
    finally:
        db.close()
    print(f"Added {result['created']} sources; {result['skipped']} already present; {len(result['errors'])} errors")
    for e in result["errors"]:
        print(f"  {e['feed_url']}: {e['error']}", file=sys.stderr)
    return 0 if not result["errors"] else 1


def cmd_import_geonames(args) -> int:
    from app.geography.gazetteer import invalidate_gazetteer
    from app.geography.seed import import_geonames

    db = _db()
    try:
        stats = import_geonames(db, Path(args.admin1), Path(args.cities), min_population=args.min_population)
        db.commit()
        invalidate_gazetteer()
        print(f"Imported: {stats}")
        return 0
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.cli", description="NEWSWIRE administration")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate").set_defaults(fn=cmd_migrate)
    sub.add_parser("bootstrap").set_defaults(fn=cmd_bootstrap)
    ca = sub.add_parser("create-admin")
    ca.add_argument("--email")
    ca.add_argument("--name")
    ca.set_defaults(fn=cmd_create_admin)
    rp = sub.add_parser("reset-password")
    rp.add_argument("email")
    rp.set_defaults(fn=cmd_reset_password)
    bk = sub.add_parser("backup")
    bk.add_argument("--no-images", action="store_true")
    bk.set_defaults(fn=cmd_backup)
    vb = sub.add_parser("verify-backup")
    vb.add_argument("path")
    vb.set_defaults(fn=cmd_verify)
    rs = sub.add_parser("restore")
    rs.add_argument("path")
    rs.add_argument("--database-url", help="Restore into this database instead of NEWSWIRE_DATABASE_URL")
    rs.add_argument("--no-images", action="store_true")
    rs.add_argument("--yes", action="store_true", help="Confirm that the target will be overwritten")
    rs.set_defaults(fn=cmd_restore)
    gn = sub.add_parser("import-geonames")
    gn.add_argument("admin1")
    gn.add_argument("cities")
    gn.add_argument("--min-population", type=int, default=15000)
    gn.set_defaults(fn=cmd_import_geonames)
    sp = sub.add_parser("import-sources")
    sp.add_argument("file", nargs="?")
    sp.add_argument("--pack")
    sp.set_defaults(fn=cmd_import_sources)
    sub.add_parser("list-source-packs").set_defaults(fn=cmd_list_source_packs)
    args = p.parse_args(argv)
    from app.core.logging import configure_logging

    configure_logging("cli")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
