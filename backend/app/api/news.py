from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, literal_column, or_, select
from sqlalchemy.orm import Session

from app.api.common import get_or_404, news_out
from app.core.db import get_db, is_postgres, utcnow
from app.core.pagination import PageParams
from app.geography.gazetteer import get_gazetteer
from app.models import (
    AssignmentMatch,
    Category,
    EditorialAssignment,
    GeographicEntity,
    NewsGeography,
    NewsItem,
    NewsItemSource,
    Source,
    StoryCluster,
    StoryClusterItem,
    source_categories,
)
from app.schemas.domain import EventLocationIn, NewsGeoOut, NewsReviewIn
from app.security import permissions as P
from app.security.deps import CurrentUser, require
from app.services.audit import record

router = APIRouter(prefix="/news", tags=["news"])


FTS_VECTOR_SQL = "to_tsvector('simple'::regconfig, coalesce(news_items.title, '') || ' ' || coalesce(news_items.summary, ''))"


def fts_condition(db: Session, q: str):
    """PostgreSQL full-text search (simple config, multilingual); LIKE fallback on SQLite."""
    if is_postgres(db):
        # Literal SQL so the expression matches the GIN index `ix_news_items_fts` exactly.
        vector = literal_column(FTS_VECTOR_SQL)
        return vector.op("@@")(func.websearch_to_tsquery(literal_column("'simple'::regconfig"), q))
    like = f"%{q}%"
    return or_(NewsItem.title.ilike(like), NewsItem.summary.ilike(like))


def build_news_query(
    db: Session,
    *,
    q: str | None = None,
    source_ids: list[uuid.UUID] | None = None,
    publisher: str | None = None,
    country: list[str] | None = None,
    region: str | None = None,
    category_id: uuid.UUID | None = None,
    language: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    duplicates: str = "include",
    matched: str | None = None,
    assignment_id: uuid.UUID | None = None,
    review_status: str | None = None,
    since: datetime | None = None,
):
    stmt = select(NewsItem)
    if q:
        stmt = stmt.where(fts_condition(db, q.strip()))
    if source_ids:
        linked = select(NewsItemSource.news_item_id).where(NewsItemSource.source_id.in_(source_ids))
        stmt = stmt.where(or_(NewsItem.source_id.in_(source_ids), NewsItem.id.in_(linked)))
    if publisher:
        stmt = stmt.where(NewsItem.source_id.in_(select(Source.id).where(Source.publisher == publisher)))
    if country:
        codes = [c.upper() for c in country]
        geo = select(NewsGeography.news_item_id).where(
            NewsGeography.country_code.in_(codes), NewsGeography.confidence >= 0.5,
            NewsGeography.role.in_(("mentioned", "event", "article", "editor")))
        stmt = stmt.where(NewsItem.id.in_(geo))
    if region:
        stmt = stmt.where(NewsItem.id.in_(select(NewsGeography.news_item_id).where(
            or_(NewsGeography.region.ilike(f"%{region}%"), NewsGeography.city.ilike(f"%{region}%")))))
    if category_id:
        stmt = stmt.where(NewsItem.source_id.in_(select(source_categories.c.source_id).where(source_categories.c.category_id == category_id)))
    if language:
        stmt = stmt.where(NewsItem.language.ilike(f"{language.lower()}%"))
    if date_from:
        stmt = stmt.where(NewsItem.sort_at >= datetime.combine(date_from, datetime.min.time(), tzinfo=timezone.utc))
    if date_to:
        stmt = stmt.where(NewsItem.sort_at <= datetime.combine(date_to, datetime.max.time(), tzinfo=timezone.utc))
    if duplicates == "exclude":
        stmt = stmt.where(NewsItem.is_duplicate.is_(False))
    elif duplicates == "only":
        stmt = stmt.where(NewsItem.is_duplicate.is_(True))
    if matched == "yes":
        stmt = stmt.where(NewsItem.id.in_(select(AssignmentMatch.news_item_id)))
    elif matched == "no":
        stmt = stmt.where(~NewsItem.id.in_(select(AssignmentMatch.news_item_id)))
    if assignment_id:
        stmt = stmt.where(NewsItem.id.in_(select(AssignmentMatch.news_item_id).where(AssignmentMatch.assignment_id == assignment_id)))
    if review_status:
        stmt = stmt.where(NewsItem.review_status == review_status)
    if since:
        stmt = stmt.where(NewsItem.retrieved_at > since)
    return stmt


@router.get("")
def list_news(
    q: str | None = Query(None, max_length=200),
    source_id: list[uuid.UUID] | None = Query(None),
    publisher: str | None = None,
    country: list[str] | None = Query(None),
    region: str | None = None,
    category_id: uuid.UUID | None = None,
    language: str | None = None,
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    duplicates: Literal["include", "exclude", "only"] = "include",
    matched: Literal["yes", "no"] | None = None,
    assignment_id: uuid.UUID | None = None,
    review_status: Literal["unreviewed", "verified", "archived"] | None = None,
    since: datetime | None = None,
    sort: Literal["latest", "oldest", "retrieved"] = "latest",
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require(P.READ)),
):
    stmt = build_news_query(db, q=q, source_ids=source_id, publisher=publisher, country=country, region=region,
                            category_id=category_id, language=language, date_from=date_from, date_to=date_to,
                            duplicates=duplicates, matched=matched, assignment_id=assignment_id,
                            review_status=review_status, since=since)
    order = {
        "latest": [NewsItem.sort_at.desc(), NewsItem.id.desc()],
        "oldest": [NewsItem.sort_at.asc(), NewsItem.id.asc()],
        "retrieved": [NewsItem.retrieved_at.desc(), NewsItem.id.desc()],
    }[sort]
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = list(db.scalars(stmt.order_by(*order).offset(params.offset).limit(params.page_size)))
    latest = db.scalar(select(func.max(NewsItem.retrieved_at)))
    return {"items": news_out(db, items), "total": total, "page": params.page, "page_size": params.page_size,
            "latest_retrieved_at": latest, "server_time": utcnow()}


@router.get("/new-count")
def new_count(since: datetime, db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.READ))):
    """Cheap poll for the live feed: how many items arrived after `since`."""
    n = db.scalar(select(func.count()).select_from(NewsItem).where(NewsItem.retrieved_at > since)) or 0
    return {"count": n, "server_time": utcnow()}


@router.get("/{item_id}")
def get_news_item(item_id: uuid.UUID, db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.READ))):
    item = get_or_404(db, NewsItem, item_id, "News item")
    base = news_out(db, [item])[0].model_dump(mode="json")
    geos = list(db.scalars(select(NewsGeography).where(NewsGeography.news_item_id == item.id).order_by(NewsGeography.confidence.desc())))
    seen_in = db.execute(
        select(Source.id, Source.name, Source.publisher, NewsItemSource.first_seen_at)
        .join(NewsItemSource, NewsItemSource.source_id == Source.id)
        .where(NewsItemSource.news_item_id == item.id)
    ).all()
    dup_ids = [item.duplicate_of_id] if item.duplicate_of_id else []
    duplicates = list(db.scalars(select(NewsItem).where(or_(NewsItem.duplicate_of_id == item.id, NewsItem.id.in_(dup_ids)))))
    cluster = db.scalar(select(StoryCluster).join(StoryClusterItem).where(StoryClusterItem.news_item_id == item.id))
    matches = db.execute(
        select(AssignmentMatch.id, AssignmentMatch.assignment_id, EditorialAssignment.headline, AssignmentMatch.score,
               AssignmentMatch.explanation, AssignmentMatch.review_status)
        .join(EditorialAssignment, EditorialAssignment.id == AssignmentMatch.assignment_id)
        .where(AssignmentMatch.news_item_id == item.id)
    ).all()
    base.update(
        content=item.content,
        guid=item.guid,
        extra=item.extra,
        geographies=[NewsGeoOut.model_validate(g).model_dump(mode="json") for g in geos],
        seen_in=[{"source_id": str(r[0]), "name": r[1], "publisher": r[2], "first_seen_at": r[3]} for r in seen_in],
        duplicates=[d.model_dump(mode="json") for d in news_out(db, duplicates)],
        cluster={"id": str(cluster.id), "title": cluster.title, "item_count": cluster.item_count} if cluster else None,
        matches=[{"id": str(m[0]), "assignment_id": str(m[1]), "headline": m[2], "score": m[3], "explanation": m[4],
                  "review_status": m[5]} for m in matches],
    )
    return base


@router.patch("/{item_id}")
def review_news_item(item_id: uuid.UUID, body: NewsReviewIn, db: Session = Depends(get_db),
                     current: CurrentUser = Depends(require(P.RESEARCH_WRITE))):
    item = get_or_404(db, NewsItem, item_id, "News item")
    item.review_status = body.review_status
    record(db, "news.reviewed", f"Marked report as {body.review_status}: {item.title[:100]}", actor=current,
           entity_type="news_item", entity_id=item.id)
    db.commit()
    return news_out(db, [item])[0]


@router.post("/{item_id}/geographies")
def set_location(item_id: uuid.UUID, body: EventLocationIn, db: Session = Depends(get_db),
                 current: CurrentUser = Depends(require(P.RESEARCH_WRITE))):
    """An editor records the event location explicitly (method=editor, confidence 1.0)."""
    item = get_or_404(db, NewsItem, item_id, "News item")
    ent = get_or_404(db, GeographicEntity, body.entity_id, "Place")
    gaz = get_gazetteer(db)
    for g in db.scalars(select(NewsGeography).where(and_(NewsGeography.news_item_id == item.id,
                                                          NewsGeography.role == body.role, NewsGeography.method == "editor"))):
        db.delete(g)
    city = ent.name if ent.kind == "city" else None
    region = ent.name if ent.kind in ("admin1", "admin2") else None
    db.add(NewsGeography(news_item_id=item.id, entity_id=ent.id, role=body.role, method="editor", confidence=1.0,
                         original_text=ent.name, country_code=ent.country_code or gaz.country_of(ent.id), city=city, region=region))
    record(db, "news.location_set", f"{body.role.title()} location set to {ent.name} for: {item.title[:80]}",
           actor=current, entity_type="news_item", entity_id=item.id)
    db.commit()
    return {"ok": True}


@router.get("/facets/publishers")
def publisher_facets(db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.READ))):
    rows = db.execute(select(Source.publisher, func.count(Source.id)).where(Source.deleted_at.is_(None))
                      .group_by(Source.publisher).order_by(Source.publisher)).all()
    return [{"publisher": r[0], "sources": r[1]} for r in rows]


__all__ = ["router", "build_news_query", "fts_condition", "Category"]
