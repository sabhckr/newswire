"""Worker scheduler. Runs in its own process (python -m app.worker) so that the API or the
web UI can be down without stopping ingestion. Every run is recorded in `scheduled_jobs`."""

from __future__ import annotations

import logging
import os
import socket
import time
from datetime import datetime, timedelta
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.collectors.ingest import run_collection_cycle
from app.core.config import get_settings
from app.core.db import SessionLocal, utcnow
from app.models import ArticlePublication, Backup, Notification, ScheduledJob, SourceFetchLog, UserSession
from app.publishing.service import run_publishing_cycle, sync_publication
from app.services import backups
from app.services.settings import get_setting

log = logging.getLogger(__name__)
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


def _record(name: str, fn: Callable[[Session], object], interval: int | None = None) -> None:
    t0 = time.monotonic()
    db = SessionLocal()
    status, error = "ok", None
    try:
        result = fn(db)
        db.commit()
        if isinstance(result, dict) and result.get("errors"):
            status = "partial"
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        status, error = "error", f"{type(exc).__name__}: {exc}"[:2000]
        log.exception("Job %s failed", name)
    finally:
        db.close()
    db = SessionLocal()
    try:
        row = db.get(ScheduledJob, name) or ScheduledJob(name=name)
        db.add(row)
        now = utcnow()
        row.last_run_at = now
        row.last_status = status
        row.last_error = error
        row.last_duration_ms = int((time.monotonic() - t0) * 1000)
        row.run_count = (row.run_count or 0) + 1
        row.failure_count = (row.failure_count or 0) + (1 if status == "error" else 0)
        row.worker_id = WORKER_ID
        row.heartbeat_at = now
        if interval:
            row.interval_seconds = interval
            row.next_run_at = now + timedelta(seconds=interval)
        db.commit()
    finally:
        db.close()


def heartbeat() -> None:
    db = SessionLocal()
    try:
        row = db.get(ScheduledJob, "worker")
        if row is None:
            row = ScheduledJob(name="worker", description="Worker process heartbeat", started_at=utcnow())
            db.add(row)
        if row.worker_id != WORKER_ID:
            row.started_at = utcnow()
        row.worker_id = WORKER_ID
        row.heartbeat_at = utcnow()
        row.last_status = "ok"
        db.commit()
    finally:
        db.close()


def job_collection(db: Session):
    return run_collection_cycle(db)


def job_publishing(db: Session):
    return run_publishing_cycle(db)


def job_backups(db: Session):
    cfg = get_setting(db, "backups")
    if not cfg["enabled"]:
        return {"skipped": "disabled"}
    last = db.scalar(select(Backup).where(Backup.status == "completed", Backup.kind == "scheduled")
                     .order_by(Backup.created_at.desc()).limit(1))
    if last and last.created_at > utcnow() - timedelta(hours=float(cfg["interval_hours"])):
        return {"skipped": "not due"}
    b = backups.create_backup(db, kind="scheduled")
    return {"backup": b.filename, "status": b.status}


def job_housekeeping(db: Session):
    now = utcnow()
    cfg = get_setting(db, "collection")
    r1 = db.execute(delete(UserSession).where((UserSession.expires_at < now) | (UserSession.revoked_at < now - timedelta(days=7))))
    r2 = db.execute(delete(SourceFetchLog).where(SourceFetchLog.started_at < now - timedelta(days=int(cfg["fetch_log_retention_days"]))))
    r3 = db.execute(delete(Notification).where(Notification.is_read.is_(True), Notification.created_at < now - timedelta(days=30)))
    return {"sessions": r1.rowcount, "fetch_logs": r2.rowcount, "notifications": r3.rowcount}


def job_publication_sync(db: Session):
    pubs = list(db.scalars(select(ArticlePublication).where(ArticlePublication.remote_status.in_(("published", "draft")))))
    for p in pubs:
        if p.destination.kind == "wordpress":
            sync_publication(db, p)
    return {"checked": len(pubs)}


def job_specs() -> list[tuple[str, Callable[[Session], object], int]]:
    s = get_settings()
    return [
        ("collection", job_collection, s.scheduler_tick_seconds),
        ("publishing", job_publishing, 30),
        ("backups", job_backups, 3600),
        ("housekeeping", job_housekeeping, 3600),
        ("publication_sync", job_publication_sync, 1800),
    ]


def register_jobs(specs: list[tuple[str, Callable[[Session], object], int]], first_run: dict[str, datetime]) -> None:
    """Record each job's schedule up front, so the System page shows it before the first run."""
    db = SessionLocal()
    try:
        for name, _fn, interval in specs:
            row = db.get(ScheduledJob, name) or ScheduledJob(name=name)
            db.add(row)
            row.interval_seconds = interval
            row.next_run_at = first_run[name]
        db.commit()
    except Exception:  # noqa: BLE001 - informational only; the scheduler still starts
        db.rollback()
        log.exception("Could not register scheduled jobs")
    finally:
        db.close()


def build_scheduler(blocking: bool):
    sched = (BlockingScheduler if blocking else BackgroundScheduler)(timezone="UTC")
    now = utcnow()
    specs = job_specs()
    first_run = {name: now + timedelta(seconds=2 + i * 3) for i, (name, _fn, _iv) in enumerate(specs)}
    register_jobs(specs, first_run)
    for name, fn, interval in specs:
        sched.add_job(
            _record, IntervalTrigger(seconds=interval), args=[name, fn, interval], id=name, name=name,
            max_instances=1, coalesce=True, misfire_grace_time=interval,
            next_run_time=first_run[name],
        )
    sched.add_job(heartbeat, IntervalTrigger(seconds=15), id="heartbeat", max_instances=1, coalesce=True,
                  next_run_time=now)
    return sched
