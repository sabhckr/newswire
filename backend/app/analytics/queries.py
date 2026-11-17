"""Aggregate reports. Every number comes from a database query; nothing is estimated."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, distinct, func, select
from sqlalchemy.orm import Session

from app.core.db import is_postgres, utcnow
from app.models import (
    Article,
    AssignmentMatch,
    Category,
    EditorialAssignment,
    NewsGeography,
    NewsItem,
    PublishingJob,
    ScheduledJob,
    Source,
    SourceFetchLog,
    source_categories,
)


def _zone(tz_name: str | None):
    """The viewer's IANA time zone, or UTC when absent or unknown."""
    try:
        return ZoneInfo(tz_name) if tz_name else timezone.utc
    except Exception:  # noqa: BLE001 - unknown names fall back to UTC
        return timezone.utc


def zone_name(tz_name: str | None) -> str:
    zone = _zone(tz_name)
    return getattr(zone, "key", "UTC")


def _sqlite_shift(tz_name: str | None) -> str:
    # SQLite has no time zone database: use the zone's current UTC offset (development only).
    offset = datetime.now(_zone(tz_name)).utcoffset() or timedelta(0)
    return f"{int(offset.total_seconds() // 60):+d} minutes"


def day_bucket(db: Session, col, tz: str | None = None):
    """Calendar day of a timestamp in the given time zone (UTC by default)."""
    if is_postgres(db):
        return func.to_char(func.date_trunc("day", func.timezone(zone_name(tz), col)), "YYYY-MM-DD")
    return func.strftime("%Y-%m-%d", col, _sqlite_shift(tz))


def hour_bucket(db: Session, col, tz: str | None = None):
    if is_postgres(db):
        return func.to_char(func.date_trunc("hour", func.timezone(zone_name(tz), col)), 'YYYY-MM-DD"T"HH24:00')
    return func.strftime("%Y-%m-%dT%H:00", col, _sqlite_shift(tz))


def start_of_today(tz_name: str | None) -> datetime:
    tz = _zone(tz_name)
    now_local = datetime.now(tz)
    return datetime.combine(now_local.date(), time.min, tzinfo=tz).astimezone(timezone.utc)


def date_range(start: date | None, end: date | None, default_days: int = 30, tz: str | None = None) -> tuple[datetime, datetime]:
    """Whole calendar days in the viewer's time zone, as UTC instants."""
    zone = _zone(tz)
    end_dt = datetime.combine(end, time.max, tzinfo=zone).astimezone(timezone.utc) if end else utcnow()
    start_dt = (
        datetime.combine(start, time.min, tzinfo=zone).astimezone(timezone.utc)
        if start else end_dt - timedelta(days=default_days)
    )
    return start_dt, end_dt


def worker_status(db: Session) -> dict:
    row = db.get(ScheduledJob, "worker")
    now = utcnow()
    if row is None or row.heartbeat_at is None:
        return {"state": "not_running", "heartbeat_at": None, "worker_id": None}
    age = (now - row.heartbeat_at).total_seconds()
    state = "running" if age < 120 else "stale"
    return {"state": state, "heartbeat_at": row.heartbeat_at, "worker_id": row.worker_id, "age_seconds": int(age),
            "started_at": row.started_at}


def overview(db: Session, tz: str | None = None) -> dict:
    today = start_of_today(tz)
    count = lambda stmt: db.scalar(stmt) or 0  # noqa: E731
    active_sources = count(select(func.count()).select_from(Source).where(Source.is_active.is_(True), Source.deleted_at.is_(None)))
    failed_sources = count(select(func.count()).select_from(Source).where(
        Source.is_active.is_(True), Source.deleted_at.is_(None), Source.consecutive_failures > 0))
    last_collection = db.scalar(select(func.max(SourceFetchLog.finished_at)))
    next_poll = db.scalar(select(func.min(Source.next_fetch_at)).where(Source.is_active.is_(True), Source.deleted_at.is_(None)))
    collection_job = db.get(ScheduledJob, "collection")
    return {
        "as_of": utcnow(),
        "day_start": today,
        "status": {
            "worker": worker_status(db),
            "collection_enabled": True,
            "active_sources": active_sources,
            "failed_sources": failed_sources,
            "last_collection_at": last_collection,
            "next_poll_at": next_poll,
            "last_cycle_status": collection_job.last_status if collection_job else None,
            "pending_editorial_reviews": count(select(func.count()).select_from(Article).where(
                Article.status == "in_review", Article.deleted_at.is_(None))),
        },
        "metrics": {
            "articles_collected_today": count(select(func.count()).select_from(NewsItem).where(NewsItem.retrieved_at >= today)),
            "new_matches_today": count(select(func.count()).select_from(AssignmentMatch).where(AssignmentMatch.matched_at >= today)),
            "active_assignments": count(select(func.count()).select_from(EditorialAssignment).where(EditorialAssignment.status == "active")),
            "distinct_publishers_today": count(
                select(func.count(distinct(Source.publisher))).select_from(NewsItem)
                .join(Source, Source.id == NewsItem.source_id).where(NewsItem.retrieved_at >= today)),
            "pending_research_reviews": count(select(func.count()).select_from(AssignmentMatch).where(
                AssignmentMatch.review_status == "unreviewed")),
            "draft_articles": count(select(func.count()).select_from(Article).where(
                Article.status.in_(("draft", "in_review")), Article.deleted_at.is_(None))),
            "published_articles": count(select(func.count()).select_from(Article).where(
                Article.status == "published", Article.deleted_at.is_(None))),
        },
    }


def news_volume(db: Session, start: datetime, end: datetime, tz: str | None = None) -> list[dict]:
    hourly = (end - start) <= timedelta(days=2)
    bucket = (hour_bucket if hourly else day_bucket)(db, NewsItem.retrieved_at, tz)
    rows = db.execute(
        select(bucket.label("bucket"), func.count().label("items"),
               func.sum(case((NewsItem.is_duplicate.is_(True), 1), else_=0)).label("duplicates"))
        .where(NewsItem.retrieved_at.between(start, end)).group_by(bucket).order_by(bucket)
    ).all()
    return [{"bucket": r.bucket, "items": int(r.items), "duplicates": int(r.duplicates or 0)} for r in rows]


def by_country(db: Session, start: datetime, end: datetime, role: str | None = None, limit: int = 50, tz: str | None = None) -> list[dict]:
    roles = [role] if role else ["mentioned", "event", "article", "editor"]
    rows = db.execute(
        select(NewsGeography.country_code, func.count(distinct(NewsGeography.news_item_id)).label("items"))
        .join(NewsItem, NewsItem.id == NewsGeography.news_item_id)
        .where(NewsItem.retrieved_at.between(start, end), NewsGeography.role.in_(roles),
               NewsGeography.confidence >= 0.5, NewsGeography.country_code.is_not(None))
        .group_by(NewsGeography.country_code).order_by(func.count(distinct(NewsGeography.news_item_id)).desc()).limit(limit)
    ).all()
    return [{"country_code": r[0], "items": int(r[1])} for r in rows]


def by_publisher(db: Session, start: datetime, end: datetime, limit: int = 50, tz: str | None = None) -> list[dict]:
    rows = db.execute(
        select(Source.publisher, func.count(NewsItem.id).label("items"),
               func.sum(case((NewsItem.is_duplicate.is_(True), 1), else_=0)).label("duplicates"),
               func.max(NewsItem.retrieved_at).label("latest"))
        .join(Source, Source.id == NewsItem.source_id)
        .where(NewsItem.retrieved_at.between(start, end))
        .group_by(Source.publisher).order_by(func.count(NewsItem.id).desc()).limit(limit)
    ).all()
    return [{"publisher": r[0], "items": int(r[1]), "duplicates": int(r[2] or 0), "latest": r[3]} for r in rows]


def by_category(db: Session, start: datetime, end: datetime, tz: str | None = None) -> list[dict]:
    rows = db.execute(
        select(Category.name, func.count(distinct(NewsItem.id)))
        .join(source_categories, source_categories.c.category_id == Category.id)
        .join(NewsItem, NewsItem.source_id == source_categories.c.source_id)
        .where(NewsItem.retrieved_at.between(start, end))
        .group_by(Category.name).order_by(func.count(distinct(NewsItem.id)).desc())
    ).all()
    out = [{"category": r[0], "items": int(r[1])} for r in rows]
    uncategorised = db.scalar(
        select(func.count(NewsItem.id)).where(
            NewsItem.retrieved_at.between(start, end),
            ~NewsItem.source_id.in_(select(source_categories.c.source_id)),
        )
    ) or 0
    if uncategorised:
        out.append({"category": "Uncategorised source", "items": int(uncategorised)})
    return out


def assignment_performance(db: Session, start: datetime, end: datetime, tz: str | None = None) -> list[dict]:
    rows = db.execute(
        select(
            EditorialAssignment.id, EditorialAssignment.headline, EditorialAssignment.status,
            func.count(AssignmentMatch.id),
            func.sum(case((AssignmentMatch.review_status == "verified", 1), else_=0)),
            func.sum(case((AssignmentMatch.review_status == "rejected", 1), else_=0)),
            func.avg(AssignmentMatch.score),
            func.max(AssignmentMatch.matched_at),
        )
        .outerjoin(AssignmentMatch, and_(AssignmentMatch.assignment_id == EditorialAssignment.id,
                                         AssignmentMatch.matched_at.between(start, end)))
        .where(EditorialAssignment.status != "archived")
        .group_by(EditorialAssignment.id, EditorialAssignment.headline, EditorialAssignment.status)
        .order_by(func.count(AssignmentMatch.id).desc())
    ).all()
    out = []
    for r in rows:
        total, verified, rejected = int(r[3]), int(r[4] or 0), int(r[5] or 0)
        reviewed = verified + rejected
        out.append({
            "assignment_id": str(r[0]), "headline": r[1], "status": r[2], "matches": total,
            "verified": verified, "rejected": rejected,
            "precision": round(verified / reviewed, 3) if reviewed else None,
            "avg_score": round(float(r[6]), 1) if r[6] is not None else None, "last_match": r[7],
        })
    return out


def source_reliability(db: Session, start: datetime, end: datetime, tz: str | None = None) -> list[dict]:
    rows = db.execute(
        select(
            Source.id, Source.name, Source.publisher, Source.is_active, Source.consecutive_failures,
            func.count(SourceFetchLog.id),
            func.sum(case((SourceFetchLog.status.in_(("success", "not_modified")), 1), else_=0)),
            func.sum(case((SourceFetchLog.status.in_(("error", "blocked")), 1), else_=0)),
            func.avg(SourceFetchLog.duration_ms),
            func.sum(SourceFetchLog.items_new),
        )
        .outerjoin(SourceFetchLog, and_(SourceFetchLog.source_id == Source.id, SourceFetchLog.started_at.between(start, end)))
        .where(Source.deleted_at.is_(None))
        .group_by(Source.id, Source.name, Source.publisher, Source.is_active, Source.consecutive_failures)
        .order_by(Source.name)
    ).all()
    out = []
    for r in rows:
        fetches, ok, err = int(r[5]), int(r[6] or 0), int(r[7] or 0)
        out.append({
            "source_id": str(r[0]), "name": r[1], "publisher": r[2], "is_active": r[3],
            "consecutive_failures": r[4], "fetches": fetches, "successes": ok, "failures": err,
            "success_rate": round(ok / fetches, 3) if fetches else None,
            "avg_duration_ms": int(r[8]) if r[8] is not None else None, "new_items": int(r[9] or 0),
        })
    return out


def publication_activity(db: Session, start: datetime, end: datetime, tz: str | None = None) -> list[dict]:
    bucket = day_bucket(db, PublishingJob.completed_at, tz)
    rows = db.execute(
        select(bucket, func.sum(case((PublishingJob.status == "succeeded", 1), else_=0)),
               func.sum(case((PublishingJob.status == "failed", 1), else_=0)))
        .where(PublishingJob.completed_at.between(start, end)).group_by(bucket).order_by(bucket)
    ).all()
    return [{"bucket": r[0], "succeeded": int(r[1] or 0), "failed": int(r[2] or 0)} for r in rows]


def editorial_throughput(db: Session, start: datetime, end: datetime, tz: str | None = None) -> list[dict]:
    created_b = day_bucket(db, Article.created_at, tz)
    published_b = day_bucket(db, Article.published_at, tz)
    reviewed_b = day_bucket(db, AssignmentMatch.reviewed_at, tz)
    data: dict[str, dict] = {}

    def put(rows, key):
        for b, n in rows:
            if b:
                data.setdefault(b, {"bucket": b, "articles_created": 0, "articles_published": 0, "matches_reviewed": 0})[key] = int(n)

    put(db.execute(select(created_b, func.count()).where(Article.created_at.between(start, end)).group_by(created_b)).all(), "articles_created")
    put(db.execute(select(published_b, func.count()).where(Article.published_at.between(start, end)).group_by(published_b)).all(), "articles_published")
    put(db.execute(select(reviewed_b, func.count()).where(AssignmentMatch.reviewed_at.between(start, end)).group_by(reviewed_b)).all(), "matches_reviewed")
    return [data[k] for k in sorted(data)]


REPORTS = {
    "volume": news_volume,
    "countries": by_country,
    "publishers": by_publisher,
    "categories": by_category,
    "assignments": assignment_performance,
    "sources": source_reliability,
    "publications": publication_activity,
    "throughput": editorial_throughput,
}
