"""Research workspace: everything known about one assignment, aggregated."""

from __future__ import annotations

import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics.queries import day_bucket, hour_bucket
from app.api.assignments import _out as assignment_out
from app.api.common import get_or_404
from app.core.db import get_db
from app.images.service import warnings_for
from app.models import (
    AssignmentMatch,
    EditorialAssignment,
    ImageAsset,
    NewsGeography,
    NewsItem,
    Source,
    StoryCluster,
    StoryClusterItem,
)
from app.models.articles import Article, article_assignments
from app.schemas.domain import ImageOut, ResearchUpdateIn
from app.security import permissions as P
from app.security.deps import CurrentUser, require
from app.services.articles import create_from_assignment
from app.services.audit import record

router = APIRouter(prefix="/research", tags=["research"])


def image_out(asset: ImageAsset, *, commercial: bool = True) -> ImageOut:
    from app.articles.content import media_url

    o = ImageOut.model_validate(asset)
    o.local_url = media_url(asset) if asset.local_path else None
    o.local_thumbnail_url = media_url(asset, thumb=True) if asset.thumbnail_path else None
    o.warnings = warnings_for(asset, commercial=commercial)
    return o


@router.get("/{assignment_id}")
def workspace(assignment_id: uuid.UUID, db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.READ))):
    a = get_or_404(db, EditorialAssignment, assignment_id, "Assignment")
    base = (
        select(AssignmentMatch.news_item_id)
        .where(AssignmentMatch.assignment_id == a.id, AssignmentMatch.review_status.in_(("unreviewed", "verified")))
    )
    stats = db.execute(
        select(func.count(NewsItem.id), func.count(func.distinct(Source.publisher)), func.min(NewsItem.sort_at),
               func.max(NewsItem.sort_at))
        .select_from(NewsItem).outerjoin(Source, Source.id == NewsItem.source_id).where(NewsItem.id.in_(base))
    ).one()
    by_status = dict(db.execute(
        select(AssignmentMatch.review_status, func.count()).where(AssignmentMatch.assignment_id == a.id)
        .group_by(AssignmentMatch.review_status)).all())
    avg_score = db.scalar(select(func.avg(AssignmentMatch.score)).where(AssignmentMatch.assignment_id == a.id))

    publishers = db.execute(
        select(Source.publisher, func.count(NewsItem.id), func.min(NewsItem.sort_at), func.max(NewsItem.sort_at),
               func.max(AssignmentMatch.score))
        .select_from(AssignmentMatch)
        .join(NewsItem, NewsItem.id == AssignmentMatch.news_item_id)
        .join(Source, Source.id == NewsItem.source_id)
        .where(AssignmentMatch.assignment_id == a.id, AssignmentMatch.review_status != "rejected")
        .group_by(Source.publisher).order_by(func.min(NewsItem.sort_at))
    ).all()

    countries = db.execute(
        select(NewsGeography.country_code, NewsGeography.role, func.count(func.distinct(NewsGeography.news_item_id)))
        .where(NewsGeography.news_item_id.in_(base), NewsGeography.confidence >= 0.5, NewsGeography.country_code.is_not(None))
        .group_by(NewsGeography.country_code, NewsGeography.role)
    ).all()
    geo_summary: dict[str, dict] = defaultdict(dict)
    for cc, role, n in countries:
        geo_summary[cc][role] = int(n)

    span_hours = ((stats[3] - stats[2]).total_seconds() / 3600) if stats[2] and stats[3] else 0
    bucket_fn = hour_bucket if span_hours <= 72 else day_bucket
    bucket = bucket_fn(db, NewsItem.sort_at)
    timeline = db.execute(
        select(bucket, func.count(NewsItem.id), func.count(func.distinct(NewsItem.source_id)))
        .where(NewsItem.id.in_(base)).group_by(bucket).order_by(bucket)
    ).all()

    clusters = db.execute(
        select(StoryCluster.id, StoryCluster.title, StoryCluster.title_source, StoryCluster.item_count,
               StoryCluster.publisher_count, StoryCluster.first_seen_at, StoryCluster.last_seen_at,
               func.count(StoryClusterItem.news_item_id))
        .join(StoryClusterItem, StoryClusterItem.cluster_id == StoryCluster.id)
        .where(StoryClusterItem.news_item_id.in_(base))
        .group_by(StoryCluster.id, StoryCluster.title, StoryCluster.title_source, StoryCluster.item_count,
                  StoryCluster.publisher_count, StoryCluster.first_seen_at, StoryCluster.last_seen_at)
        .order_by(StoryCluster.last_seen_at.desc()).limit(30)
    ).all()

    images = list(db.scalars(select(ImageAsset).where(ImageAsset.assignment_id == a.id).order_by(ImageAsset.created_at.desc())))
    articles = db.execute(
        select(Article.id, Article.headline, Article.status, Article.updated_at)
        .join(article_assignments, article_assignments.c.article_id == Article.id)
        .where(article_assignments.c.assignment_id == a.id, Article.deleted_at.is_(None))
    ).all()

    return {
        "assignment": assignment_out(db, [a])[0].model_dump(mode="json"),
        "stats": {
            "matched_items": int(stats[0] or 0),
            "distinct_publishers": int(stats[1] or 0),
            "first_report_at": stats[2],
            "latest_report_at": stats[3],
            "by_review_status": {k: int(v) for k, v in by_status.items()},
            "average_score": round(float(avg_score), 1) if avg_score is not None else None,
        },
        "publishers": [
            {"publisher": p[0], "items": int(p[1]), "first_report_at": p[2], "latest_report_at": p[3], "top_score": p[4]}
            for p in publishers
        ],
        "geography": [{"country_code": cc, "roles": roles} for cc, roles in sorted(geo_summary.items())],
        "timeline": {
            "granularity": "hour" if bucket_fn is hour_bucket else "day",
            "buckets": [{"bucket": t[0], "items": int(t[1]), "publishers": int(t[2])} for t in timeline],
        },
        "clusters": [
            {"id": str(c[0]), "title": c[1], "title_source": c[2], "item_count": c[3], "publisher_count": c[4],
             "first_seen_at": c[5], "last_seen_at": c[6], "matched_items": int(c[7])}
            for c in clusters
        ],
        "images": [image_out(i).model_dump(mode="json") for i in images],
        "articles": [{"id": str(r[0]), "headline": r[1], "status": r[2], "updated_at": r[3]} for r in articles],
    }


@router.patch("/{assignment_id}")
def update_workspace(assignment_id: uuid.UUID, body: ResearchUpdateIn, db: Session = Depends(get_db),
                     current: CurrentUser = Depends(require(P.RESEARCH_WRITE))):
    a = get_or_404(db, EditorialAssignment, assignment_id, "Assignment")
    if body.editorial_notes is not None:
        a.editorial_notes = body.editorial_notes
    if body.source_checklist is not None:
        a.source_checklist = [
            {"label": str(i.get("label", ""))[:300], "done": bool(i.get("done")), "url": i.get("url")}
            for i in body.source_checklist[:100]
        ]
    if body.research_status is not None and body.research_status != a.research_status:
        a.research_status = body.research_status
        record(db, "research.status", f"Research for “{a.headline[:100]}” marked {body.research_status}",
               actor=current, entity_type="assignment", entity_id=a.id)
    db.commit()
    return {"ok": True, "editorial_notes": a.editorial_notes, "source_checklist": a.source_checklist,
            "research_status": a.research_status}


@router.post("/{assignment_id}/article", status_code=201)
def start_article(assignment_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.ARTICLES_WRITE))):
    a = get_or_404(db, EditorialAssignment, assignment_id, "Assignment")
    article = create_from_assignment(db, a, current)
    record(db, "article.created", f"Draft “{article.headline[:100]}” created from research template",
           actor=current, entity_type="article", entity_id=article.id, details={"assignment_id": str(a.id)})
    db.commit()
    from app.api.articles import article_out

    return article_out(db, article, current)
