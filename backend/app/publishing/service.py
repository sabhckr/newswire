"""Publication queue with idempotency, approval gates, retries and audit."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Callable

import httpx
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.articles.content import publication_checks
from app.core.db import SessionLocal, is_postgres, utcnow
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
WEBSITE_KINDS = ("builtin", "wordpress", "webhook", "website")
# Sites people read articles on (Facebook, Instagram and YouTube posts link to them).
ARTICLE_SITE_KINDS = ("wordpress", "webhook", "website")
SOCIAL_KINDS = ("facebook", "instagram", "youtube")
# Remote states that can be taken down again.
UNPUBLISHABLE_STATES = ("published", "draft", "private", "processing")
# A social post waits this long for the website publication it links to.
LINK_WAIT = timedelta(minutes=30)
# A job left "running" this long was interrupted (the process stopped mid-upload).
STALE_RUNNING = timedelta(hours=3)


def ensure_builtin_destination(db: Session) -> PublishingDestination:
    dest = db.scalar(select(PublishingDestination).where(PublishingDestination.kind == "builtin"))
    if dest is None:
        dest = PublishingDestination(name="NEWSWIRE public API", kind="builtin", is_active=True, is_commercial=True)
        db.add(dest)
        db.flush()
    return dest


def _key(*parts) -> str:
    return hashlib.sha256(":".join(str(p) for p in parts).encode()).hexdigest()


def check_publication(
    db: Session, article: Article, dest: PublishingDestination, action: str, *, actor, mode: str = "immediate",
    scheduled_for: datetime | None = None, system: bool = False, options: dict | None = None,
) -> dict:
    """Raise AppError if the article cannot go to this destination; return the normalised options.
    Writes nothing, so several destinations can all be checked before any job is created."""
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
        from app.publishing.social import validate_options

        try:
            options = validate_options(dest, article, options, mode)
        except ValueError as exc:
            raise AppError(422, "destination_options", str(exc)) from exc
    else:
        options = {}
        pub = db.scalar(select(ArticlePublication).where(
            ArticlePublication.article_id == article.id, ArticlePublication.destination_id == dest.id))
        if pub is None or pub.remote_status not in UNPUBLISHABLE_STATES:
            raise AppError(409, "not_published", "The article is not published at this destination")
    return options


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
    options: dict | None = None,
) -> tuple[PublishingJob, bool]:
    """Queue a publishing action. Returns (job, created). Duplicate requests return the existing job."""
    options = check_publication(db, article, dest, action, actor=actor, mode=mode, scheduled_for=scheduled_for,
                                system=system, options=options)
    key = _key(article.id, dest.id, action, mode, article.version, scheduled_for.isoformat() if scheduled_for else "",
               json.dumps(options, sort_keys=True), article.video_id or "")
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
        options=options or None,
    )
    try:
        with db.begin_nested():
            db.add(job)
            db.flush()
    except IntegrityError:
        return db.scalar(select(PublishingJob).where(PublishingJob.idempotency_key == key)), False
    if mode == "scheduled":
        # An article that is already live somewhere stays "published" while a later post waits.
        if article.status == "approved":
            article.status = "scheduled"
        sync_schedule_state(db, article)
    record(db, f"publishing.{job.action}_requested",
           f"{job.action.title()} requested for “{article.headline[:80]}” at {dest.name}"
           + (f" for {scheduled_for.isoformat()}" if scheduled_for else ""),
           actor=None if system else actor, entity_type="article", entity_id=article.id,
           details={"job_id": str(job.id), "destination": dest.name, "mode": mode, "version": article.version})
    db.flush()
    return job, True


def sync_schedule_state(db: Session, article: Article) -> None:
    """article.scheduled_at = the next queued scheduled publication; leave "scheduled" when none is left."""
    db.flush()
    nxt = db.scalar(select(func.min(PublishingJob.scheduled_for)).where(
        PublishingJob.article_id == article.id, PublishingJob.action == "publish",
        PublishingJob.status.in_(("pending", "running")), PublishingJob.scheduled_for.is_not(None)))
    article.scheduled_at = nxt
    if nxt is None and article.status == "scheduled":
        article.status = "approved"


def _claim(db: Session, job: PublishingJob) -> bool:
    """Atomically move a pending job to running, so the API and the worker never both run it."""
    res = db.execute(
        update(PublishingJob)
        .where(PublishingJob.id == job.id, PublishingJob.status == "pending")
        .values(status="running", attempts=PublishingJob.attempts + 1, updated_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    db.commit()  # releases the write lock before any network call (SQLite has one writer)
    db.refresh(job)
    return res.rowcount == 1


def _website_link(db: Session, article: Article, prefer: set[uuid.UUID]) -> str | None:
    """The article's address on the user's website, for Facebook, Instagram and YouTube to link to."""
    rows = db.execute(
        select(ArticlePublication.destination_id, ArticlePublication.remote_url)
        .join(PublishingDestination, PublishingDestination.id == ArticlePublication.destination_id)
        .where(ArticlePublication.article_id == article.id, ArticlePublication.remote_status == "published",
               ArticlePublication.remote_url.is_not(None), PublishingDestination.kind.in_(ARTICLE_SITE_KINDS))
        .order_by(ArticlePublication.last_synced_at.desc())
    ).all()
    for dest_id, url in rows:
        if dest_id in prefer:
            return url
    return rows[0][1] if rows else None


def process_job(db: Session, job: PublishingJob, client_factory: ClientFactory | None = None) -> PublishingJob:
    if not _claim(db, job):
        return job  # another process has it, or it was cancelled meanwhile
    article = db.get(Article, job.article_id)
    dest = job.destination
    now = utcnow()
    publication = db.scalar(select(ArticlePublication).where(
        ArticlePublication.article_id == job.article_id, ArticlePublication.destination_id == job.destination_id))
    options = dict(job.options or {})
    after = []
    for x in options.pop("after_jobs", None) or []:
        try:
            after.append(uuid.UUID(str(x)))
        except ValueError:
            continue
    if after and job.action == "publish":
        waiting = db.scalar(select(func.count()).select_from(PublishingJob).where(
            PublishingJob.id.in_(after), PublishingJob.status.in_(("pending", "running")))) or 0
        if waiting and now - (job.scheduled_for or job.created_at) < LINK_WAIT:
            # Wait for the website so the post can link to the article; this is not a failed attempt.
            job.status = "pending"
            job.attempts = max(0, job.attempts - 1)
            job.next_attempt_at = now + timedelta(seconds=20)
            db.commit()
            return job
    if dest.kind in SOCIAL_KINDS and article is not None:
        prefer = set(db.scalars(select(PublishingJob.destination_id).where(PublishingJob.id.in_(after)))) if after else set()
        options["link"] = _website_link(db, article, prefer)
    db.commit()  # end the read transaction; the network call below can take minutes
    try:
        if article is None or article.deleted_at is not None:
            raise PublishError("Article no longer exists", retryable=False)
        if job.action in ("publish", "draft"):
            allowed = PUBLISHABLE | ({"in_review"} if job.action == "draft" else set())
            if article.status not in allowed:
                raise PublishError(f"Article is no longer approved (status {article.status})", retryable=False)
        connector = connector_for(dest)
        factory = client_factory or (lambda: make_client(timeout=60))
        with factory() as client:
            if job.action == "unpublish":
                result = connector.unpublish(article, publication, client)
            else:
                result = connector.publish(article, publication, "draft" if job.action == "draft" else "immediate",
                                           client, options)
    except Exception as exc:  # noqa: BLE001 - every failure is recorded on the job
        db.rollback()
        db.refresh(job)
        retryable = getattr(exc, "retryable", True)
        msg = str(exc) if isinstance(exc, PublishError) else describe_error(exc)
        job.last_error = msg[:2000]
        if retryable and job.attempts < job.max_attempts:
            job.status = "pending"
            job.next_attempt_at = now + timedelta(seconds=60 * (2 ** (job.attempts - 1)))
        else:
            job.status = "failed"
            job.completed_at = now
            if article is not None:
                sync_schedule_state(db, article)
            notify(db, "publishing_failed", f"Publishing to {dest.name} failed: {article.headline[:100] if article else job.article_id}",
                   body=msg[:500], link=f"/articles/{job.article_id}" if article else "/publishing")
        record(db, "publishing.failed", f"{job.action} to {dest.name} failed (attempt {job.attempts}): {msg[:200]}",
               entity_type="article", entity_id=job.article_id,
               details={"job_id": str(job.id), "retry": job.status == "pending"})
        db.flush()
        return job

    db.refresh(job)
    if job.status != "running":
        # Cancelled while the request was in flight; the remote change happened, so record it anyway.
        log.warning("Job %s finished after being marked %s", job.id, job.status)
    if publication is None:
        publication = db.scalar(select(ArticlePublication).where(
            ArticlePublication.article_id == article.id, ArticlePublication.destination_id == dest.id))
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
        sync_schedule_state(db, article)
    elif job.action == "unpublish":
        db.flush()
        still_live = db.scalar(select(ArticlePublication.id).where(
            ArticlePublication.article_id == article.id, ArticlePublication.remote_status.in_(("published", "processing"))))
        if still_live is None:
            article.status = "unpublished"
    record(db, f"publishing.{job.action}_succeeded",
           f"{job.action.title()} of “{article.headline[:80]}” at {dest.name} succeeded",
           entity_type="article", entity_id=article.id,
           details={"job_id": str(job.id), "remote_id": result.remote_id, "remote_url": result.remote_url,
                    "version": article.version})
    warning = (result.details or {}).get("warning")
    if job.action == "draft":
        title = f"Draft sent to {dest.name}: {article.headline[:100]}"
    elif job.action == "unpublish":
        title = f"Removed from {dest.name}: {article.headline[:100]}"
    else:
        title = f"Published to {dest.name}: {article.headline[:100]}"
    notify(db, "published" if job.action == "publish" else "publishing", title, body=warning,
           link=f"/articles/{article.id}")
    db.flush()
    return job


def claim_due_jobs(db: Session, limit: int = 10) -> list[PublishingJob]:
    # Websites first, then Facebook/YouTube, so a social post can link to the article it announces.
    stmt = (
        select(PublishingJob)
        .join(PublishingDestination, PublishingDestination.id == PublishingJob.destination_id)
        .where(PublishingJob.status == "pending", PublishingJob.next_attempt_at <= utcnow())
        .order_by(case((PublishingDestination.kind.in_(SOCIAL_KINDS), 1), else_=0), PublishingJob.next_attempt_at.asc())
        .limit(limit)
    )
    if is_postgres(db):
        stmt = stmt.with_for_update(skip_locked=True, of=PublishingJob)
    return list(db.scalars(stmt).unique())


def recover_interrupted(db: Session) -> int:
    """Jobs stuck in "running" (the process stopped mid-request) are failed, not silently re-run."""
    now = utcnow()
    n = 0
    for job in db.scalars(select(PublishingJob).where(
            PublishingJob.status == "running", PublishingJob.updated_at < now - STALE_RUNNING)):
        job.status = "failed"
        job.completed_at = now
        job.last_error = ("Interrupted: NEWSWIRE stopped while this was being published. Check the destination; "
                          "retry if it did not arrive.")
        article = db.get(Article, job.article_id)
        if article is not None:
            sync_schedule_state(db, article)
        n += 1
    db.flush()
    return n


def run_publishing_cycle(db: Session, client_factory: ClientFactory | None = None) -> dict:
    stats = {"processed": 0, "succeeded": 0, "failed": 0, "retrying": 0, "waiting": 0}
    if recover_interrupted(db):
        db.commit()
    jobs = claim_due_jobs(db)
    db.commit()
    for job in jobs:
        process_job(db, job, client_factory)
        db.commit()
        if job.status == "pending" and job.attempts == 0:
            stats["waiting"] += 1
            continue
        stats["processed"] += 1
        stats[{"succeeded": "succeeded", "failed": "failed"}.get(job.status, "retrying")] += 1
    # Facebook and YouTube process uploaded videos for a while; check on them every few minutes.
    cutoff = utcnow() - timedelta(minutes=2)
    for pub in db.scalars(select(ArticlePublication).where(
            ArticlePublication.remote_status == "processing",
            or_(ArticlePublication.last_synced_at.is_(None), ArticlePublication.last_synced_at < cutoff)).limit(5)):
        sync_publication(db, pub, client_factory)
        db.commit()
    return stats


def process_in_background(job_ids: list[uuid.UUID]) -> None:
    """Run queued uploads after the HTTP response; the worker picks up anything left over."""
    db = SessionLocal()
    try:
        for jid in job_ids:
            job = db.get(PublishingJob, jid)
            if job is not None and job.status == "pending" and (job.next_attempt_at is None or job.next_attempt_at <= utcnow()):
                process_job(db, job)
                db.commit()
    except Exception:  # noqa: BLE001 - the job stays queued for the worker
        log.exception("Background publishing failed")
        db.rollback()
    finally:
        db.close()


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
    if article is not None:
        sync_schedule_state(db, article)
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
        if res.details:
            publication.details = res.details
        publication.sync_error = (res.details or {}).get("error") if res.remote_status == "failed" else None
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


def request_many(
    db: Session, article: Article, targets: list[tuple[PublishingDestination, dict | None]], *, actor,
    mode: str = "immediate", scheduled_for: datetime | None = None,
) -> list[tuple[PublishingDestination, PublishingJob, bool]]:
    """Queue one publication per destination, all or nothing.

    Websites are queued first; Facebook and YouTube jobs of the same request wait for them, so
    they can link to the article on the website.
    """
    ids = [d.id for d, _ in targets]
    if len(set(ids)) != len(ids):
        raise AppError(422, "validation_error", "Each destination can be chosen once")
    ordered = sorted(targets, key=lambda t: t[0].kind in SOCIAL_KINDS)
    out: list[tuple[PublishingDestination, PublishingJob, bool]] = []
    # Check every destination before creating any job (on SQLite a released savepoint is already
    # committed, so a later rollback could not take back an earlier destination's job).
    for dest, opts in ordered:
        try:
            check_publication(db, article, dest, "publish", actor=actor, mode=mode, scheduled_for=scheduled_for,
                              options=opts)
        except AppError as exc:
            raise AppError(exc.status, exc.code, f"{dest.name}: {exc.message}", exc.details) from exc
    try:
        for dest, opts in ordered:
            try:
                job, created = request_publication(db, article, dest, "publish", actor=actor, mode=mode,
                                                   scheduled_for=scheduled_for, options=opts)
            except AppError as exc:
                raise AppError(exc.status, exc.code, f"{dest.name}: {exc.message}", exc.details) from exc
            out.append((dest, job, created))
        websites = [str(j.id) for d, j, _ in out if d.kind in ARTICLE_SITE_KINDS and j.status in ("pending", "running")]
        for dest, job, created in out:
            if created and websites and dest.kind in SOCIAL_KINDS:
                job.options = {**(job.options or {}), "after_jobs": websites}
        db.flush()
    except AppError:
        db.rollback()
        raise
    return out
