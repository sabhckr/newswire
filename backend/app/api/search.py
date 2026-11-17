"""Global search across news, assignments, clusters, articles and sources."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.api.news import fts_condition
from app.core.db import get_db
from app.models import Article, EditorialAssignment, NewsItem, SearchHistory, Source, StoryCluster
from app.security import permissions as P
from app.security.deps import CurrentUser, require

router = APIRouter(prefix="/search", tags=["search"])


@router.get("")
def search(q: str = Query(min_length=2, max_length=200), limit: int = Query(6, ge=1, le=25), remember: bool = True,
           db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.READ))):
    term = q.strip()
    like = f"%{term}%"
    news = list(db.scalars(select(NewsItem).where(fts_condition(db, term)).order_by(NewsItem.sort_at.desc()).limit(limit)))
    assignments = list(db.scalars(select(EditorialAssignment).where(
        or_(EditorialAssignment.headline.ilike(like), EditorialAssignment.description.ilike(like)))
        .order_by(EditorialAssignment.updated_at.desc()).limit(limit)))
    clusters = list(db.scalars(select(StoryCluster).where(StoryCluster.title.ilike(like))
                               .order_by(StoryCluster.last_seen_at.desc()).limit(limit)))
    articles = list(db.scalars(select(Article).where(Article.deleted_at.is_(None), or_(
        Article.headline.ilike(like), Article.subtitle.ilike(like))).order_by(Article.updated_at.desc()).limit(limit)))
    sources = list(db.scalars(select(Source).where(Source.deleted_at.is_(None), or_(
        Source.name.ilike(like), Source.publisher.ilike(like), Source.feed_url.ilike(like))).limit(limit)))
    if remember:
        last = db.scalar(select(SearchHistory).where(SearchHistory.user_id == current.id)
                         .order_by(SearchHistory.created_at.desc()).limit(1))
        if last is None or last.query != term:
            db.add(SearchHistory(user_id=current.id, query=term[:255]))
            db.commit()
    return {
        "query": term,
        "groups": [
            {"type": "news", "label": "News", "items": [
                {"id": str(n.id), "title": n.title, "subtitle": n.source.publisher if n.source else None,
                 "href": f"/live?item={n.id}", "at": n.sort_at} for n in news]},
            {"type": "assignments", "label": "Headlines", "items": [
                {"id": str(a.id), "title": a.headline, "subtitle": a.status, "href": f"/research/{a.id}", "at": a.updated_at}
                for a in assignments]},
            {"type": "clusters", "label": "Clusters", "items": [
                {"id": str(c.id), "title": c.title, "subtitle": f"{c.item_count} reports · {c.publisher_count} publishers",
                 "href": f"/clusters?id={c.id}", "at": c.last_seen_at} for c in clusters]},
            {"type": "articles", "label": "Articles", "items": [
                {"id": str(a.id), "title": a.headline, "subtitle": a.status, "href": f"/articles/{a.id}", "at": a.updated_at}
                for a in articles]},
            {"type": "sources", "label": "Sources", "items": [
                {"id": str(s.id), "title": s.name, "subtitle": s.publisher, "href": f"/sources?id={s.id}", "at": s.updated_at}
                for s in sources]},
        ],
    }


@router.get("/history")
def history(db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.READ))):
    rows = db.scalars(select(SearchHistory).where(SearchHistory.user_id == current.id)
                      .order_by(SearchHistory.created_at.desc()).limit(50))
    seen: list[str] = []
    for r in rows:
        if r.query not in seen:
            seen.append(r.query)
    return {"items": seen[:15]}


@router.delete("/history")
def clear_history(db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.READ))):
    db.execute(delete(SearchHistory).where(SearchHistory.user_id == current.id))
    db.commit()
    return {"ok": True}
