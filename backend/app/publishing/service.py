"""Publication queue with idempotency, approval gates, retries and audit."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
from typing import Callable

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.articles.content import publication_checks
from app.core.db import is_postgres, utcnow
from app.core.errors import AppError
from app.core.http import describe_error, make_client
from app.models import Article, ArticlePublication, PublishingDestination, PublishingJob
from app.publishing.connectors import PublishError, connector_for
from app.security import permissions as P
from app.services.audit import notify, record
from app.services.settings import get_setting

log = logging.getLogger(__name__)

ClientFactory = Callable[[], httpx.Client]
PUBLISHABLE = {"approved", "scheduled", "published", "unpublished"}


def ensure_builtin_destination(db: Session) -> PublishingDestination:
    dest = db.scalar(select(PublishingDestination).where(PublishingDestination.kind == "builtin"))
    if dest is None:
        dest = PublishingDestination(name="NEWSWIRE public API", kind="builtin", is_active=True, is_commercial=True)
        db.add(dest)
        db.flush()
    return dest


def _key(*parts) -> str:
    return hashlib.sha256(":".join(str(p) for p in parts).encode()).hexdigest()


def request_publication(
    db: Session,
    article: Article,
    dest: PublishingDestination,
    action: str,
    *,
    actor,
    mode: str = "immediate",
    scheduled_for: datetime | None = None,
    system: bool = False,
) -> tuple[PublishingJob, bool]:
    """Queue a publishing action. Returns (job, created). Duplicate requests return the existing job."""
    if action not in ("publish", "unpublish"):
        raise AppError(400, "bad_request", "Unknown publishing action")
    if mode not in ("immediate", "draft", "scheduled"):
        raise AppError(400, "bad_request", "Unknown publishing mode")
    if not dest.is_active:
        raise AppError(409, "destination_inactive", "This destination is disabled")
    if not system and not actor.can(P.ARTICLES_PUBLISH):
        raise AppError(403, "forbidden", "Requires permission: articles:publish")

    if action == "publish":
        policy = get_setting(db, "publishing")
        allowed = PUBLISHABLE | ({"in_review"} if mode == "draft" else set())
        if article.status not in allowed:
            raise AppError(409, "not_approved",
                           f"Only approved articles can be published (current status: {article.status})")
        checks = publication_checks(article, commercial=dest.is_commercial,
                                    block_on_markers=policy.get("block_on_template_markers", True))
        errors = [c for c in checks if c["level"] == "error"]
        if errors and mode != "draft":
            raise AppError(409, "checks_failed", "Pre-publication checks failed", errors)
        license_errors = [c for c in errors if c["code"] not in ("body_too_short", "template_markers")]
        if license_errors and mode == "draft":
            raise AppError(409, "checks_failed", "Image licence checks failed", license_errors)
        if mode == "scheduled":
            if scheduled_for is None or scheduled_for <= utcnow():
                raise AppError(422, "validation_error", "A future publication time is required")
    else:
        pub = db.scalar(select(ArticlePublication).where(
            ArticlePublication.article_id == article.id, ArticlePublication.destination_id == dest.id))
        if pub is None or pub.remote_status not in ("published", "draft"):
            raise AppError(409, "not_published", "The article is not published at this destination")

    key = _key(article.id, dest.id, action, mode, article.version, scheduled_for.isoformat() if scheduled_for else "")
    existing = db.scalar(select(PublishingJob).where(PublishingJob.idempotency_key == key))
    if existing is not None and existing.status not in ("failed", "cancelled"):
        return existing, False
    if existing is not None:
        key = _key(key, utcnow().isoformat())  # explicit re-request after a failed/cancelled attempt
    job = PublishingJob(
        article_id=article.id,
        destination_id=dest.id,
        destination=dest,
        action="draft" if (action == "publish" and mode == "draft") else action,
        status="pending",
        idempotency_key=key,
        article_version=article.version,
        scheduled_for=scheduled_for,
        next_attempt_at=scheduled_for or utcnow(),
        requested_by_id=getattr(actor, "id", None),
    )
    try:
        with db.begin_nested():
            db.add(job)
            db.flush()
    except IntegrityError:
        return db.scalar(select(PublishingJob).where(PublishingJob.idempotency_key == key)), False
    if mode == "scheduled":
        article.status = "scheduled"
        article.scheduled_at = scheduled_for
    record(db, f"publishing.{job.action}_requested",
           f"{job.action.title()} requested for “{article.headline[:80]}” at {dest.name}"
           + (f" for {scheduled_for.isoformat()}" if scheduled_for else ""),
           actor=None if system else actor, entity_type="article", entity_id=article.id,
           details={"job_id": str(job.id), "destination": dest.name, "mode": mode, "version": article.version})
    db.flush()
    return job, True


def process_job(db: Session, job: PublishingJob, client_factory: ClientFactory | None = None) -> PublishingJob:
    article = db.get(Article, job.article_id)
    dest = job.destination
    now = utcnow()
    job.status = "running"
    job.attempts += 1
    db.flush()
    publication = db.scalar(select(ArticlePublication).where(
        ArticlePublication.article_id == job.article_id, ArticlePublication.destination_id == job.destination_id))
    try:
        if article is None or article.deleted_at is not None:
            raise PublishError("Article no longer exists", retryable=False)
        if job.action in ("publish", "draft"):
            allowed = PUBLISHABLE | ({"in_review"} if job.action == "draft" else set())
            if article.status not in allowed:
                raise PublishError(f"Article is no longer approved (status {article.status})", retryable=False)
        connector = connector_for(dest)
        factory = client_factory or (lambda: make_client(timeout=30))
        with factory() as client:
            if job.action == "unpublish":
                result = connector.unpublish(article, publication, client)
            else:
                result = connector.publish(article, publication, "draft" if job.action == "draft" else "immediate", client)
    except Exception as exc:  # noqa: BLE001 - every failure is recorded on the job
        retryable = getattr(exc, "retryable", True)
        msg = str(exc) if isinstance(exc, PublishError) else describe_error(exc)
        job.last_error = msg[:2000]
        if retryable and job.attempts < job.max_attempts:
            job.status = "pending"
            job.next_attempt_at = now + timedelta(seconds=60 * (2 ** (job.attempts - 1)))
        else:
            job.status = "failed"
            job.completed_at = now
            if article is not None and article.status == "scheduled" and job.action == "publish":
                article.status = "approved"
            notify(db, "publishing_failed", f"Publishing failed: {article.headline[:100] if article else job.article_id}",
                   body=msg[:500], link="/publishing")
        record(db, "publishing.failed", f"{job.action} to {dest.name} failed (attempt {job.attempts}): {msg[:200]}",
               entity_type="article", entity_id=job.article_id,
               details={"job_id": str(job.id), "retry": job.status == "pending"})
        db.flush()
        return job

    if publication is None:
        publication = ArticlePublication(article_id=article.id, destination_id=dest.id, remote_status=result.remote_status)
        db.add(publication)
    publication.remote_id = result.remote_id
    publication.remote_url = result.remote_url
    publication.remote_status = result.remote_status
    publication.last_synced_at = now
    publication.sync_error = None
    publication.details = result.details or publication.details
    job.status = "succeeded"
    job.completed_at = now
    job.remote_id = result.remote_id
    job.remote_url = result.remote_url
    job.last_error = None
    if job.action == "publish":
        publication.published_version = article.version
        article.status = "published"
        article.published_at = article.published_at or now
        article.last_published_version = article.version
        article.scheduled_at = None
    elif job.action == "unpublish":
        db.flush()
        still_live = db.scalar(select(ArticlePublication.id).where(
            ArticlePublication.article_id == article.id, ArticlePublication.remote_status == "published"))
        if still_live is None:
            article.status = "unpublished"
    record(db, f"publishing.{job.action}_succeeded",
           f"{job.action.title()} of “{article.headline[:80]}” at {dest.name} succeeded",
           entity_type="article", entity_id=article.id,
           details={"job_id": str(job.id), "remote_id": result.remote_id, "remote_url": result.remote_url,
                    "version": article.version})
    notify(db, "published" if job.action == "publish" else "publishing",
           f"{job.action.title()}ed: {article.headline[:100]}" if job.action != "draft" else f"Draft sent: {article.headline[:100]}",
           link=f"/articles/{article.id}")
    db.flush()
    return job


def claim_due_jobs(db: Session, limit: int = 10) -> list[PublishingJob]:
    stmt = (
        select(PublishingJob)
        .where(PublishingJob.status == "pending", PublishingJob.next_attempt_at <= utcnow())
        .order_by(PublishingJob.next_attempt_at.asc())
        .limit(limit)
    )
    if is_postgres(db):
        stmt = stmt.with_for_update(skip_locked=True, of=PublishingJob)
    return list(db.scalars(stmt).unique())


def run_publishing_cycle(db: Session, client_factory: ClientFactory | None = None) -> dict:
    jobs = claim_due_jobs(db)
    stats = {"processed": 0, "succeeded": 0, "failed": 0, "retrying": 0}
    for job in jobs:
        process_job(db, job, client_factory)
        db.commit()
        stats["processed"] += 1
        stats[{"succeeded": "succeeded", "failed": "failed"}.get(job.status, "retrying")] += 1
    return stats


def retry_job(db: Session, job: PublishingJob, actor) -> PublishingJob:
    """Failed/cancelled jobs get three more attempts; a pending job waiting out its backoff runs now."""
    if job.status not in ("failed", "cancelled", "pending"):
        raise AppError(409, "conflict", "Only pending, failed or cancelled jobs can be retried")
    if job.scheduled_for is not None and job.scheduled_for > utcnow():
        # Running it now would publish before the time an editor chose.
        raise AppError(409, "conflict", "This publication is scheduled for later. Cancel it, or publish again from the article to change the time")
    if job.status != "pending":
        job.max_attempts = job.attempts + 3
    job.status = "pending"
    job.next_attempt_at = utcnow()
    record(db, "publishing.retry", f"Retry requested for job {job.id}", actor=actor, entity_type="article",
           entity_id=job.article_id, details={"job_id": str(job.id)})
    db.flush()
    return job


def cancel_job(db: Session, job: PublishingJob, actor) -> PublishingJob:
    if job.status != "pending":
        raise AppError(409, "conflict", "Only pending jobs can be cancelled")
    job.status = "cancelled"
    job.completed_at = utcnow()
    article = db.get(Article, job.article_id)
    if article and article.status == "scheduled":
        article.status = "approved"
        article.scheduled_at = None
    record(db, "publishing.cancelled", f"Cancelled {job.action} job {job.id}", actor=actor,
           entity_type="article", entity_id=job.article_id, details={"job_id": str(job.id)})
    db.flush()
    return job


def sync_publication(db: Session, publication: ArticlePublication, client_factory: ClientFactory | None = None) -> ArticlePublication:
    article = db.get(Article, publication.article_id)
    try:
        connector = connector_for(publication.destination)
        factory = client_factory or (lambda: make_client(timeout=30))
        with factory() as client:
            res = connector.status(article, publication, client)
        publication.remote_status = res.remote_status
        publication.remote_url = res.remote_url or publication.remote_url
        publication.sync_error = None
    except Exception as exc:  # noqa: BLE001
        publication.sync_error = str(exc)[:500]
    publication.last_synced_at = utcnow()
    db.flush()
    return publication


def auto_publish_if_enabled(db: Session, article: Article) -> list[PublishingJob]:
    """Only when an admin has enabled the auto-publish policy and the article is approved."""
    if not get_setting(db, "publishing").get("auto_publish_policy") or article.status != "approved":
        return []
    jobs = []
    for dest in db.scalars(select(PublishingDestination).where(
        PublishingDestination.is_active.is_(True), PublishingDestination.auto_publish.is_(True))):
        try:
            job, created = request_publication(db, article, dest, "publish", actor=None, system=True)
            if created:
                jobs.append(job)
        except AppError as exc:
            record(db, "publishing.auto_skipped", f"Auto-publish to {dest.name} skipped: {exc.message}",
                   entity_type="article", entity_id=article.id)
    return jobs
