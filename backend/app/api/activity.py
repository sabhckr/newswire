"""Audit trail and fetch history, with CSV export."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.common import csv_response
from app.core.db import get_db
from app.core.pagination import PageParams
from app.models import AuditLog, Notification, Source, SourceFetchLog
from app.schemas.common import AuditOut
from app.security import permissions as P
from app.security.deps import CurrentUser, require

router = APIRouter(tags=["activity"])


def _range(stmt, col, start: date | None, end: date | None):
    if start:
        stmt = stmt.where(col >= datetime.combine(start, time.min, tzinfo=timezone.utc))
    if end:
        stmt = stmt.where(col <= datetime.combine(end, time.max, tzinfo=timezone.utc))
    return stmt


@router.get("/activity/audit")
def audit(
    action: str | None = Query(None, max_length=64),
    entity_type: str | None = Query(None, max_length=32),
    entity_id: str | None = Query(None, max_length=64),
    actor_id: uuid.UUID | None = None,
    q: str | None = Query(None, max_length=200),
    start: date | None = None,
    end: date | None = None,
    format: Literal["json", "csv"] = "json",
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require(P.READ)),
):
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action}%"))
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(AuditLog.summary.ilike(like), AuditLog.actor_label.ilike(like)))
    stmt = _range(stmt, AuditLog.created_at, start, end)
    if format == "csv":
        rows = db.scalars(stmt.order_by(AuditLog.created_at.desc()).limit(50_000))
        return csv_response(
            ({"time": r.created_at.isoformat(), "actor": r.actor_label, "action": r.action, "entity_type": r.entity_type,
              "entity_id": r.entity_id, "summary": r.summary, "ip": r.ip} for r in rows),
            "newswire-audit.csv",
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(db.scalars(stmt.order_by(AuditLog.created_at.desc()).offset(params.offset).limit(params.page_size)))
    return {"items": [AuditOut.model_validate(r) for r in rows], "total": total, "page": params.page, "page_size": params.page_size}


@router.get("/activity/fetches")
def fetches(
    source_id: uuid.UUID | None = None,
    status: str | None = Query(None, max_length=32),
    start: date | None = None,
    end: date | None = None,
    format: Literal["json", "csv"] = "json",
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require(P.READ)),
):
    stmt = select(SourceFetchLog, Source.name, Source.publisher).join(Source, Source.id == SourceFetchLog.source_id)
    if source_id:
        stmt = stmt.where(SourceFetchLog.source_id == source_id)
    if status:
        stmt = stmt.where(SourceFetchLog.status == status)
    stmt = _range(stmt, SourceFetchLog.started_at, start, end)
    stmt = stmt.order_by(SourceFetchLog.started_at.desc())

    def row(log: SourceFetchLog, name: str, publisher: str) -> dict:
        return {"id": str(log.id), "source_id": str(log.source_id), "source_name": name, "publisher": publisher,
                "trigger": log.trigger, "status": log.status, "http_status": log.http_status,
                "started_at": log.started_at.isoformat(), "duration_ms": log.duration_ms, "items_found": log.items_found,
                "items_new": log.items_new, "items_duplicate": log.items_duplicate, "items_skipped": log.items_skipped,
                "bytes_received": log.bytes_received, "error_message": log.error_message}

    if format == "csv":
        return csv_response((row(*r) for r in db.execute(stmt.limit(50_000))), "newswire-fetches.csv")
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = db.execute(stmt.offset(params.offset).limit(params.page_size)).all()
    return {"items": [row(*r) for r in rows], "total": total, "page": params.page, "page_size": params.page_size}


# ---- Notifications --------------------------------------------------------

def _visible(current: CurrentUser):
    return or_(Notification.user_id.is_(None), Notification.user_id == current.id)


@router.get("/notifications")
def notifications(unread_only: bool = False, limit: int = Query(30, ge=1, le=200), db: Session = Depends(get_db),
                  current: CurrentUser = Depends(require(P.READ))):
    stmt = select(Notification).where(_visible(current))
    unread = db.scalar(select(func.count()).select_from(Notification).where(_visible(current), Notification.is_read.is_(False))) or 0
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    rows = list(db.scalars(stmt.order_by(Notification.created_at.desc()).limit(limit)))
    return {
        "unread": unread,
        "items": [{"id": str(n.id), "kind": n.kind, "title": n.title, "body": n.body, "link": n.link, "is_read": n.is_read,
                   "created_at": n.created_at} for n in rows],
    }


@router.post("/notifications/{notification_id}/read")
def mark_read(notification_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.READ))):
    n = db.scalar(select(Notification).where(Notification.id == notification_id, _visible(current)))
    if n is not None:
        n.is_read = True
        db.commit()
    return {"ok": True}


@router.post("/notifications/read-all")
def mark_all_read(db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.READ))):
    for n in db.scalars(select(Notification).where(_visible(current), Notification.is_read.is_(False))):
        n.is_read = True
    db.commit()
    return {"ok": True}
