from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.common import get_or_404
from app.core.crypto import encrypt_secret
from app.core.db import get_db, utcnow
from app.core.errors import AppError
from app.core.http import UnsafeURLError, make_client, validate_url
from app.core.pagination import PageParams
from app.models import Article, ArticlePublication, PublishingDestination, PublishingJob
from app.publishing.connectors import PublishError, connector_for
from app.publishing.service import cancel_job, retry_job, run_publishing_cycle, sync_publication
from app.schemas.domain import DestinationIn, DestinationOut, DestinationUpdateIn, JobOut, PublicationOut
from app.security import permissions as P
from app.security.deps import CurrentUser, require
from app.services.audit import record

router = APIRouter(prefix="/publishing", tags=["publishing"])


def dest_out(d: PublishingDestination) -> DestinationOut:
    o = DestinationOut.model_validate(d)
    o.has_secret = bool(d.secret_encrypted)
    return o


def _check(url: str) -> None:
    try:
        validate_url(url)
    except UnsafeURLError as exc:
        raise AppError(422, "unsafe_url", str(exc)) from exc


@router.get("/destinations", response_model=list[DestinationOut])
def destinations(db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.READ))):
    return [dest_out(d) for d in db.scalars(select(PublishingDestination).order_by(PublishingDestination.created_at))]


@router.post("/destinations", response_model=DestinationOut, status_code=201)
def create_destination(body: DestinationIn, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.PUBLISHING_MANAGE))):
    _check(body.base_url)
    if body.kind == "wordpress" and not (body.username and body.secret):
        raise AppError(422, "validation_error", "WordPress needs a username and an application password")
    d = PublishingDestination(name=body.name, kind=body.kind, base_url=body.base_url.rstrip("/"), username=body.username,
                              secret_encrypted=encrypt_secret(body.secret) if body.secret else None,
                              is_active=body.is_active, is_commercial=body.is_commercial, auto_publish=body.auto_publish)
    db.add(d)
    db.flush()
    record(db, "destination.created", f"Publishing destination {d.name} ({d.kind}) added", actor=current,
           entity_type="destination", entity_id=d.id)
    db.commit()
    return dest_out(d)


@router.put("/destinations/{dest_id}", response_model=DestinationOut)
def update_destination(dest_id: uuid.UUID, body: DestinationUpdateIn, db: Session = Depends(get_db),
                       current: CurrentUser = Depends(require(P.PUBLISHING_MANAGE))):
    d = get_or_404(db, PublishingDestination, dest_id, "Destination")
    data = body.model_dump(exclude_unset=True)
    if d.kind == "builtin":
        data = {k: v for k, v in data.items() if k in ("name", "is_active", "is_commercial", "auto_publish")}
    if "base_url" in data and data["base_url"]:
        _check(data["base_url"])
        data["base_url"] = data["base_url"].rstrip("/")
    secret = data.pop("secret", None)
    for k, v in data.items():
        setattr(d, k, v)
    if secret:
        d.secret_encrypted = encrypt_secret(secret)
    record(db, "destination.updated", f"Publishing destination {d.name} updated" + (" (credential replaced)" if secret else ""),
           actor=current, entity_type="destination", entity_id=d.id, details={"fields": sorted(data)})
    db.commit()
    return dest_out(d)


@router.delete("/destinations/{dest_id}")
def delete_destination(dest_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.PUBLISHING_MANAGE))):
    d = get_or_404(db, PublishingDestination, dest_id, "Destination")
    if d.kind == "builtin":
        raise AppError(409, "conflict", "The built-in destination cannot be deleted; disable it instead")
    live = db.scalar(select(func.count()).select_from(ArticlePublication).where(
        ArticlePublication.destination_id == d.id, ArticlePublication.remote_status == "published")) or 0
    if live:
        raise AppError(409, "conflict", f"{live} articles are published here; unpublish them or disable the destination")
    record(db, "destination.deleted", f"Publishing destination {d.name} deleted", actor=current, entity_type="destination", entity_id=d.id)
    db.delete(d)
    db.commit()
    return {"ok": True}


@router.post("/destinations/{dest_id}/test")
def test_destination(dest_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.PUBLISHING_MANAGE))):
    d = get_or_404(db, PublishingDestination, dest_id, "Destination")
    ok, message = True, ""
    try:
        with make_client(timeout=20) as client:
            message = connector_for(d).check(client)
    except PublishError as exc:
        ok, message = False, str(exc)
    except Exception as exc:  # noqa: BLE001
        from app.core.http import describe_error

        ok, message = False, describe_error(exc)
    d.last_checked_at = utcnow()
    d.last_check_status = ("OK: " if ok else "FAILED: ") + message[:280]
    record(db, "destination.tested", f"Connection test for {d.name}: {'ok' if ok else 'failed'}", actor=current,
           entity_type="destination", entity_id=d.id)
    db.commit()
    return {"ok": ok, "message": message}


@router.get("/jobs")
def jobs(
    status: list[Literal["pending", "running", "succeeded", "failed", "cancelled"]] | None = Query(None),
    destination_id: uuid.UUID | None = None,
    article_id: uuid.UUID | None = None,
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require(P.READ)),
):
    stmt = select(PublishingJob)
    if status:
        stmt = stmt.where(PublishingJob.status.in_(status))
    if destination_id:
        stmt = stmt.where(PublishingJob.destination_id == destination_id)
    if article_id:
        stmt = stmt.where(PublishingJob.article_id == article_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(db.scalars(stmt.order_by(PublishingJob.created_at.desc()).offset(params.offset).limit(params.page_size)))
    heads = dict(db.execute(select(Article.id, Article.headline).where(Article.id.in_({j.article_id for j in rows}))).all()) if rows else {}
    items = [JobOut.model_validate(j).model_copy(update={"article_headline": heads.get(j.article_id),
                                                          "destination_name": j.destination.name}) for j in rows]
    counts = dict(db.execute(select(PublishingJob.status, func.count()).group_by(PublishingJob.status)).all())
    return {"items": items, "total": total, "page": params.page, "page_size": params.page_size,
            "status_counts": {k: int(v) for k, v in counts.items()}}


@router.post("/jobs/run")
def run_queue(db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.ARTICLES_PUBLISH))):
    stats = run_publishing_cycle(db)
    record(db, "publishing.queue_run", f"Publishing queue run manually: {stats}", actor=current, entity_type="publishing")
    db.commit()
    return stats


@router.post("/jobs/{job_id}/retry", response_model=JobOut)
def retry(job_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.ARTICLES_PUBLISH))):
    job = retry_job(db, get_or_404(db, PublishingJob, job_id, "Job"), current)
    db.commit()
    return job


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel(job_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.ARTICLES_PUBLISH))):
    job = cancel_job(db, get_or_404(db, PublishingJob, job_id, "Job"), current)
    db.commit()
    return job


@router.post("/publications/{pub_id}/sync", response_model=PublicationOut)
def sync(pub_id: uuid.UUID, db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.ARTICLES_PUBLISH))):
    p = sync_publication(db, get_or_404(db, ArticlePublication, pub_id, "Publication"))
    db.commit()
    return PublicationOut.model_validate(p).model_copy(update={"destination_name": p.destination.name,
                                                               "destination_kind": p.destination.kind})
