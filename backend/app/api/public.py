"""Read-only public API for a custom website (the built-in publishing destination)."""

from __future__ import annotations

import html
from email.utils import format_datetime

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import not_found
from app.models import Article, ArticlePublication, PublishingDestination
from app.publishing.connectors import article_payload

router = APIRouter(prefix="/public", tags=["public"])


def _published():
    builtin_live = (
        select(ArticlePublication.article_id)
        .join(PublishingDestination, PublishingDestination.id == ArticlePublication.destination_id)
        .where(PublishingDestination.kind == "builtin", PublishingDestination.is_active.is_(True),
               ArticlePublication.remote_status == "published")
    )
    return select(Article).where(Article.id.in_(builtin_live), Article.status == "published", Article.deleted_at.is_(None))


@router.get("/articles")
def public_articles(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    stmt = _published()
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(db.scalars(stmt.order_by(Article.published_at.desc()).offset((page - 1) * page_size).limit(page_size)))
    items = []
    for a in rows:
        p = article_payload(a)
        p.pop("html", None)
        p.pop("body_html", None)
        items.append(p)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/articles/{slug}")
def public_article(slug: str, db: Session = Depends(get_db)):
    a = db.scalar(_published().where(Article.slug == slug))
    if a is None:
        raise not_found("Article")
    return article_payload(a)


@router.get("/feed.xml")
def public_feed(db: Session = Depends(get_db)):
    base = get_settings().public_base_url.rstrip("/")
    rows = list(db.scalars(_published().order_by(Article.published_at.desc()).limit(50)))
    e = html.escape
    items = "".join(
        f"<item><title>{e(a.headline)}</title><link>{e(base)}/api/v1/public/articles/{e(a.slug)}</link>"
        f"<guid isPermaLink=\"false\">{a.id}</guid>"
        + (f"<pubDate>{format_datetime(a.published_at)}</pubDate>" if a.published_at else "")
        + f"<description>{e(a.excerpt or a.seo_description or a.subtitle or '')}</description></item>"
        for a in rows
    )
    xml = (f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Samyabadi</title>'
           f"<link>{e(base)}</link><description>Published articles</description>{items}</channel></rss>")
    return Response(xml, media_type="application/rss+xml")
