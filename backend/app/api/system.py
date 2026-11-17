"""Health, worker status, scheduled jobs, logs and backups."""

from __future__ import annotations

import platform
import uuid
from collections import deque
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.analytics.queries import worker_status
from app.api.common import get_or_404
from app.collectors.ingest import run_collection_cycle
from app.core.config import VERSION, get_settings
from app.core.db import get_db, utcnow
from app.core.errors import AppError
from app.core.logging import redact
from app.models import Backup, ScheduledJob, Source, SourceFetchLog
from app.security import permissions as P
from app.security.deps import CurrentUser, require
from app.services import backups as backup_svc
from app.services.audit import record

router = APIRouter(tags=["system"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    """Unauthenticated liveness/readiness probe: no internal details."""
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "database": "ok", "version": VERSION}
    except Exception:  # noqa: BLE001
        raise AppError(503, "unavailable", "Database unreachable") from None


@router.get("/system/status")
def status(db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.READ))):
    s = get_settings()
    db_version = None
    try:
        db_version = db.execute(text("SELECT version()")).scalar() if db.get_bind().dialect.name == "postgresql" \
            else "SQLite " + db.execute(text("select sqlite_version()")).scalar()
    except Exception:  # noqa: BLE001
        db.rollback()
    revision = None
    try:
        revision = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # noqa: BLE001
        db.rollback()
    jobs = list(db.scalars(select(ScheduledJob).where(ScheduledJob.name != "worker").order_by(ScheduledJob.name)))
    last_backup = db.scalar(select(Backup).where(Backup.status == "completed").order_by(Backup.created_at.desc()).limit(1))
    since = utcnow().replace(microsecond=0)
    from datetime import timedelta

    day = since - timedelta(hours=24)
    fetch_stats = db.execute(
        select(SourceFetchLog.status, func.count()).where(SourceFetchLog.started_at >= day).group_by(SourceFetchLog.status)
    ).all()
    return {
        "version": VERSION,
        "environment": s.env,
        "python": platform.python_version(),
        "database": {"dialect": db.get_bind().dialect.name, "version": db_version, "alembic_revision": revision},
        "worker": worker_status(db),
        "jobs": [
            {"name": j.name, "description": j.description, "interval_seconds": j.interval_seconds, "last_run_at": j.last_run_at,
             "last_status": j.last_status, "last_error": j.last_error, "last_duration_ms": j.last_duration_ms,
             "next_run_at": j.next_run_at, "run_count": j.run_count, "failure_count": j.failure_count}
            for j in jobs
        ],
        "fetches_24h": {k: int(v) for k, v in fetch_stats},
        "sources": {
            "active": db.scalar(select(func.count()).select_from(Source).where(Source.is_active.is_(True), Source.deleted_at.is_(None))) or 0,
            "failing": db.scalar(select(func.count()).select_from(Source).where(
                Source.is_active.is_(True), Source.deleted_at.is_(None), Source.consecutive_failures > 0)) or 0,
        },
        "storage": backup_svc.storage_usage(),
        "last_backup": {"filename": last_backup.filename, "created_at": last_backup.created_at, "size_bytes": last_backup.size_bytes,
                        "verify_status": last_backup.verify_status} if last_backup else None,
        "config": {
            "embedded_scheduler": s.embedded_scheduler, "fetch_concurrency": s.fetch_concurrency,
            "min_poll_interval_seconds": s.min_poll_interval_seconds, "allow_private_network": s.allow_private_network,
            "backup_encryption": bool(s.backup_encryption_key), "openverse_credentials": bool(s.openverse_client_id),
        },
    }


@router.post("/system/collection/run")
def run_collection_now(limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db),
                       current: CurrentUser = Depends(require(P.SOURCES_WRITE))):
    """Collect every due source now (respects robots.txt, backoff and per-host delays)."""
    stats = run_collection_cycle(db, limit=limit)
    record(db, "collection.run_now", "Collection cycle started manually", actor=current, entity_type="system", details=stats)
    db.commit()
    return stats


LOG_FILES = {"api": "newswire-api.log", "worker": "newswire-worker.log"}


def _tail(process: str, lines: int, level: str | None, q: str | None) -> list[str]:
    path = get_settings().logs_dir / LOG_FILES[process]
    if not path.exists():
        return []
    out: deque[str] = deque(maxlen=lines)
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if level and f'"level": "{level}"' not in line and f" {level} " not in line:
                continue
            if q and q.lower() not in line.lower():
                continue
            out.append(redact(line.rstrip("\n")))
    return list(out)


@router.get("/system/logs")
def logs(
    process: Literal["api", "worker"] = "worker",
    lines: int = Query(300, ge=1, le=5000),
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] | None = None,
    q: str | None = Query(None, max_length=200),
    _: CurrentUser = Depends(require(P.SYSTEM_MANAGE)),
):
    return {"process": process, "lines": _tail(process, lines, level, q)}


@router.get("/system/logs/export")
def export_logs(process: Literal["api", "worker"] = "worker", _: CurrentUser = Depends(require(P.SYSTEM_MANAGE))):
    from fastapi.responses import PlainTextResponse

    body = "\n".join(_tail(process, 100_000, None, None))
    return PlainTextResponse(body, headers={"Content-Disposition": f'attachment; filename="newswire-{process}.log"'})


# ---- Backups -------------------------------------------------------------

def _backup_out(b: Backup) -> dict:
    return {"id": str(b.id), "filename": b.filename, "kind": b.kind, "status": b.status, "size_bytes": b.size_bytes,
            "sha256": b.sha256, "encrypted": b.encrypted, "includes": b.includes, "created_at": b.created_at,
            "completed_at": b.completed_at, "verified_at": b.verified_at, "verify_status": b.verify_status, "error": b.error}


@router.get("/system/backups")
def list_backups(db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.SYSTEM_MANAGE))):
    rows = list(db.scalars(select(Backup).where(Backup.status != "deleted").order_by(Backup.created_at.desc()).limit(200)))
    return {"items": [_backup_out(b) for b in rows], "directory": str(get_settings().backups_dir)}


@router.post("/system/backups", status_code=201)
def create_backup(include_images: bool | None = None, db: Session = Depends(get_db),
                  current: CurrentUser = Depends(require(P.SYSTEM_MANAGE))):
    b = backup_svc.create_backup(db, kind="manual", user_id=current.id, include_images=include_images)
    record(db, "backup.created", f"Manual backup {b.filename}: {b.status}", actor=current, entity_type="backup", entity_id=b.id)
    db.commit()
    if b.status != "completed":
        raise AppError(500, "backup_failed", b.error or "Backup failed", {"backup": _backup_out(b)})
    return _backup_out(b)


@router.post("/system/backups/{backup_id}/verify")
def verify_backup(backup_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.SYSTEM_MANAGE))):
    b = get_or_404(db, Backup, backup_id, "Backup")
    path = get_settings().backups_dir / b.filename
    if not path.exists():
        raise AppError(404, "not_found", "Backup file is missing from the backups directory")
    ok, message = backup_svc.verify_file(path)
    b.verified_at = utcnow()
    b.verify_status = message if ok else f"FAILED: {message}"
    record(db, "backup.verified", f"Backup {b.filename} verification: {b.verify_status}", actor=current,
           entity_type="backup", entity_id=b.id)
    db.commit()
    return {"ok": ok, "message": message, "backup": _backup_out(b)}


@router.get("/system/backups/{backup_id}/download")
def download_backup(backup_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.SYSTEM_MANAGE))):
    b = get_or_404(db, Backup, backup_id, "Backup")
    path = get_settings().backups_dir / b.filename
    if b.status != "completed" or not path.exists():
        raise AppError(404, "not_found", "Backup file not available")
    record(db, "backup.downloaded", f"Backup {b.filename} downloaded", actor=current, entity_type="backup", entity_id=b.id)
    db.commit()
    return FileResponse(path, filename=b.filename, media_type="application/octet-stream")


@router.delete("/system/backups/{backup_id}")
def delete_backup(backup_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.SYSTEM_MANAGE))):
    b = get_or_404(db, Backup, backup_id, "Backup")
    if b.status == "running":
        raise AppError(409, "conflict", "This backup is still running")
    path = get_settings().backups_dir / b.filename
    if path.exists():
        path.unlink()
    b.status = "deleted"
    record(db, "backup.deleted", f"Backup {b.filename} deleted", actor=current, entity_type="backup", entity_id=b.id)
    db.commit()
    return {"ok": True}
