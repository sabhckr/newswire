"""Backups: database dump + image files + configuration export, with a hashed manifest.

Archive layout (tar.gz, optionally Fernet-encrypted as .tar.gz.enc):
  manifest.json      version, database kind, alembic revision, sha256 of every member
  database.dump      pg_dump custom format (PostgreSQL)   or  database.sqlite (SQLite)
  config.json        sources, assignments, settings, categories, destinations (no secrets)
  images/...         storage/images (optional)
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import VERSION, get_settings
from app.core.crypto import fernet_for_backups
from app.core.db import utcnow
from app.models import (
    Backup,
    Category,
    EditorialAssignment,
    GeographicEntity,
    PublishingDestination,
    Source,
    SystemSetting,
)

log = logging.getLogger(__name__)


class BackupError(Exception):
    pass


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def libpq_url(sqlalchemy_url: str) -> str:
    url = make_url(sqlalchemy_url)
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def export_config(db: Session) -> dict:
    def src(s: Source) -> dict:
        return {
            "name": s.name, "publisher": s.publisher, "source_type": s.source_type, "feed_url": s.feed_url,
            "website_url": s.website_url, "country_code": s.country_code, "region": s.region, "city": s.city,
            "language": s.language, "polling_interval_seconds": s.polling_interval_seconds, "priority": s.priority,
            "reliability_notes": s.reliability_notes, "terms_notes": s.terms_notes, "is_active": s.is_active,
            "respect_robots": s.respect_robots, "extract_page_metadata": s.extract_page_metadata,
            "api_config": s.api_config, "categories": [c.name for c in s.categories],
        }

    return {
        "exported_at": utcnow().isoformat(),
        "newswire_version": VERSION,
        "sources": [src(s) for s in db.scalars(select(Source).where(Source.deleted_at.is_(None)))],
        "categories": [{"name": c.name, "slug": c.slug, "description": c.description} for c in db.scalars(select(Category))],
        "assignments": [
            {
                "headline": a.headline, "description": a.description, "status": a.status, "priority": a.priority,
                "language": a.language, "min_score": a.min_score, "geo_mode": a.geo_mode,
                "restrict_to_sources": a.restrict_to_sources,
                "keywords": [{"term": k.term, "kind": k.kind, "weight": k.weight} for k in a.keywords],
                "sources": [s.feed_url for s in a.sources],
                "geographies": [g.name for g in a.geographies],
            }
            for a in db.scalars(select(EditorialAssignment))
        ],
        "settings": {r.key: r.value for r in db.scalars(select(SystemSetting))},
        "destinations": [
            {"name": d.name, "kind": d.kind, "base_url": d.base_url, "username": d.username,
             "is_active": d.is_active, "is_commercial": d.is_commercial, "secret": "NOT EXPORTED"}
            for d in db.scalars(select(PublishingDestination))
        ],
        "custom_geographies": [
            {"name": g.name, "kind": g.kind, "country_code": g.country_code, "description": g.description,
             "member_ids": g.member_ids}
            for g in db.scalars(select(GeographicEntity).where(GeographicEntity.is_custom.is_(True)))
        ],
    }


def _dump_database(db: Session, out_dir: Path) -> tuple[str, Path]:
    settings = get_settings()
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        target = out_dir / "database.dump"
        env = dict(os.environ)
        cmd = [settings.pg_dump_path, "--format=custom", "--no-owner", "--no-privileges",
               f"--file={target}", f"--dbname={libpq_url(str(bind.url.render_as_string(hide_password=False)))}"]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=3600)
        if proc.returncode != 0:
            raise BackupError(f"pg_dump failed: {proc.stderr.strip()[:500]}")
        return "postgresql", target
    if bind.dialect.name == "sqlite":
        target = out_dir / "database.sqlite"
        src_path = bind.url.database
        if not src_path or src_path == ":memory:":
            raise BackupError("In-memory SQLite databases cannot be backed up")
        src = sqlite3.connect(src_path)
        dst = sqlite3.connect(target)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        return "sqlite", target
    raise BackupError(f"Unsupported database: {bind.dialect.name}")


def create_backup(db: Session, *, kind: str = "manual", user_id=None, include_images: bool | None = None) -> Backup:
    from app.services.settings import get_setting

    settings = get_settings()
    cfg = get_setting(db, "backups")
    include_images = cfg["include_images"] if include_images is None else include_images
    encrypted = bool(settings.backup_encryption_key)
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    name = f"newswire-{stamp}-{uuid.uuid4().hex[:6]}.tar.gz" + (".enc" if encrypted else "")
    backup = Backup(filename=name, kind=kind, status="running", encrypted=encrypted,
                    includes=["database", "config"] + (["images"] if include_images else []), created_by_id=user_id)
    db.add(backup)
    db.commit()

    work = Path(tempfile.mkdtemp(prefix=".work-", dir=settings.backups_dir))
    try:
        db_kind, dump_path = _dump_database(db, work)
        config_path = work / "config.json"
        config_path.write_text(json.dumps(export_config(db), indent=2, default=str))
        revision = None
        try:
            revision = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
        except Exception:
            db.rollback()
        members: dict[str, Path] = {dump_path.name: dump_path, "config.json": config_path}
        if include_images and settings.images_dir.exists():
            for p in sorted(settings.images_dir.rglob("*")):
                if p.is_file():
                    members[f"images/{p.relative_to(settings.images_dir).as_posix()}"] = p
        manifest = {
            "format": 1, "created_at": utcnow().isoformat(), "newswire_version": VERSION, "database": db_kind,
            "alembic_revision": revision, "files": {arc: _sha256_file(p) for arc, p in members.items()},
        }
        tar_path = work / "archive.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            data = json.dumps(manifest, indent=2).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size = len(data)
            info.mtime = int(datetime.now().timestamp())
            tar.addfile(info, io.BytesIO(data))
            for arc, p in members.items():
                tar.add(p, arcname=arc)
        final = settings.backups_dir / name
        if encrypted:
            final.write_bytes(fernet_for_backups(settings.backup_encryption_key).encrypt(tar_path.read_bytes()))
        else:
            shutil.move(str(tar_path), final)
        os.chmod(final, 0o600)
        backup.size_bytes = final.stat().st_size
        backup.sha256 = _sha256_file(final)
        backup.status = "completed"
        backup.completed_at = utcnow()
        ok, message = verify_file(final)
        backup.verified_at = utcnow()
        backup.verify_status = message if ok else f"FAILED: {message}"
        if not ok:
            backup.status = "failed"
            backup.error = message
    except Exception as exc:
        log.exception("Backup failed")
        backup.status = "failed"
        backup.error = str(exc)[:2000]
    finally:
        shutil.rmtree(work, ignore_errors=True)
    db.commit()
    if backup.status == "completed":
        apply_retention(db, int(cfg["retention_count"]))
    return backup


def _open_archive(path: Path) -> tarfile.TarFile:
    settings = get_settings()
    data = path.read_bytes()
    if path.name.endswith(".enc"):
        if not settings.backup_encryption_key:
            raise BackupError("Backup is encrypted; set NEWSWIRE_BACKUP_ENCRYPTION_KEY to read it")
        data = fernet_for_backups(settings.backup_encryption_key).decrypt(data)
    return tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")


def verify_file(path: Path) -> tuple[bool, str]:
    """Check every member against the manifest hashes and that the database dump is readable."""
    try:
        with _open_archive(path) as tar:
            manifest = json.loads(tar.extractfile("manifest.json").read())  # type: ignore[union-attr]
            names = set(tar.getnames())
            for arc, digest in manifest["files"].items():
                if arc not in names:
                    return False, f"missing member {arc}"
                h = hashlib.sha256(tar.extractfile(arc).read()).hexdigest()  # type: ignore[union-attr]
                if h != digest:
                    return False, f"checksum mismatch for {arc}"
            with tempfile.TemporaryDirectory() as tmp:
                if manifest["database"] == "postgresql":
                    tar.extract("database.dump", tmp, filter="data")
                    proc = subprocess.run([get_settings().pg_restore_path, "--list", f"{tmp}/database.dump"],
                                          capture_output=True, text=True, timeout=600)
                    if proc.returncode != 0:
                        return False, f"pg_restore cannot read the dump: {proc.stderr[:200]}"
                else:
                    tar.extract("database.sqlite", tmp, filter="data")
                    conn = sqlite3.connect(f"{tmp}/database.sqlite")
                    res = conn.execute("PRAGMA integrity_check").fetchone()[0]
                    conn.close()
                    if res != "ok":
                        return False, f"SQLite integrity check: {res}"
        return True, f"verified {len(manifest['files'])} files"
    except BackupError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"[:300]


def apply_retention(db: Session, keep: int) -> int:
    settings = get_settings()
    done = list(db.scalars(select(Backup).where(Backup.status == "completed").order_by(Backup.created_at.desc())))
    removed = 0
    for b in done[max(keep, 1):]:
        path = settings.backups_dir / b.filename
        if path.exists():
            path.unlink()
        b.status = "deleted"
        removed += 1
    db.commit()
    return removed


def restore(path: Path, *, database_url: str, restore_images: bool = True) -> dict:
    """Restore a backup archive into `database_url` (CLI only; destructive for the target)."""
    ok, message = verify_file(path)
    if not ok:
        raise BackupError(f"Backup verification failed: {message}")
    settings = get_settings()
    with _open_archive(path) as tar, tempfile.TemporaryDirectory() as tmp:
        manifest = json.loads(tar.extractfile("manifest.json").read())  # type: ignore[union-attr]
        if manifest["database"] == "postgresql":
            if not database_url.startswith("postgresql"):
                raise BackupError("This backup holds a PostgreSQL dump; restore it into PostgreSQL")
            tar.extract("database.dump", tmp, filter="data")
            proc = subprocess.run(
                [settings.pg_restore_path, "--clean", "--if-exists", "--no-owner", "--no-privileges",
                 f"--dbname={libpq_url(database_url)}", f"{tmp}/database.dump"],
                capture_output=True, text=True, timeout=3600,
            )
            if proc.returncode != 0:
                raise BackupError(f"pg_restore failed: {proc.stderr.strip()[:500]}")
        else:
            target = make_url(database_url).database
            if not target:
                raise BackupError("SQLite target path missing")
            tar.extract("database.sqlite", tmp, filter="data")
            shutil.copyfile(f"{tmp}/database.sqlite", target)
        images = 0
        if restore_images:
            for member in tar.getmembers():
                if member.name.startswith("images/") and member.isfile():
                    rel = Path(member.name).relative_to("images")
                    dest = (settings.images_dir / rel).resolve()
                    if settings.images_dir.resolve() not in dest.parents:
                        continue  # path traversal guard
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(tar.extractfile(member).read())  # type: ignore[union-attr]
                    images += 1
    return {"database": manifest["database"], "alembic_revision": manifest.get("alembic_revision"), "images": images}


def storage_usage() -> dict:
    settings = get_settings()

    def size(p: Path) -> int:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0

    total, used, free = shutil.disk_usage(settings.storage_dir)
    return {
        "images_bytes": size(settings.images_dir),
        "backups_bytes": size(settings.backups_dir),
        "logs_bytes": size(settings.logs_dir),
        "disk_total_bytes": total,
        "disk_free_bytes": free,
    }
