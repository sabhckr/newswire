from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.common import get_or_404
from app.api.research import image_out
from app.articles.content import media_url, preview_document, publication_checks
from app.articles.state import TransitionError, available_transitions
from app.core.db import get_db, utcnow
from app.core.errors import AppError
from app.core.pagination import PageParams
from app.models import Article, ArticlePublication, AuditLog, PublishingDestination, Tag, User
from app.publishing.service import auto_publish_if_enabled, process_job, request_publication
from app.schemas.common import AuditOut
from app.schemas.domain import (
    ArticleIn,
    ArticleListOut,
    ArticleOut,
    JobOut,
    PublicationOut,
    PublishIn,
    TagOut,
    TransitionIn,
    UnpublishIn,
)
from app.security import permissions as P
from app.security.deps import CurrentUser, require
from app.services import articles as svc
from app.services.audit import record
from app.services.settings import get_setting

router = APIRouter(prefix="/articles", tags=["articles"])


def _list_out(db: Session, items: list[Article]) -> list[ArticleListOut]:
    authors = dict(db.execute(select(User.id, User.display_name).where(User.id.in_({a.author_id for a in items if a.author_id}))).all()) if items else {}
    out = []
    for a in items:
        o = ArticleListOut.model_validate(a)
        o.author_name = authors.get(a.author_id)
        if a.featured_image is not None:
            o.featured_thumbnail = media_url(a.featured_image, thumb=True) or a.featured_image.thumbnail_url
        out.append(o)
    return out


def article_out(db: Session, a: Article, current: CurrentUser) -> ArticleOut:
    db.refresh(a)
    base = _list_out(db, [a])[0].model_dump()
    policy = get_setting(db, "publishing")
    data = ArticleOut(
        **base,
        body_html=a.body_html, body_json=a.body_json, excerpt=a.excerpt, seo_title=a.seo_title,
        seo_description=a.seo_description, category_id=a.category_id, featured_image_id=a.featured_image_id,
        featured_image=image_out(a.featured_image) if a.featured_image else None,
        featured_image_caption=a.featured_image_caption, geo_entity_ids=a.geo_entity_ids, approved_at=a.approved_at,
        tags=[TagOut.model_validate(t) for t in a.tags],
        sources=[s for s in a.sources],
        images=[{"id": i.id, "image_asset_id": i.image_asset_id, "caption": i.caption, "position": i.position,
                 "is_modified": i.is_modified, "image": image_out(i.image)} for i in a.images],
    )
    data.assignment_ids = svc.assignment_ids_for(db, a.id)
    data.transitions = available_transitions(a.status, current.permissions)
    data.checks = publication_checks(a, commercial=True, block_on_markers=policy.get("block_on_template_markers", True))
    pubs = list(db.scalars(select(ArticlePublication).where(ArticlePublication.article_id == a.id)))
    data.publications = [
        PublicationOut.model_validate(p).model_copy(update={"destination_name": p.destination.name, "destination_kind": p.destination.kind})
        for p in pubs
    ]
    data.has_unpublished_changes = a.status == "published" and (a.last_published_version or 0) < a.version
    return data


@router.get("/tags", response_model=list[TagOut])
def tags(db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.READ))):
    return list(db.scalars(select(Tag).order_by(Tag.name)))


@router.get("")
def list_articles(
    status: list[Literal["draft", "in_review", "approved", "scheduled", "published", "unpublished", "archived"]] | None = Query(None),
    q: str | None = None,
    category_id: uuid.UUID | None = None,
    author_id: uuid.UUID | None = None,
    sort: Literal["updated", "created", "published", "headline"] = "updated",
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require(P.READ)),
):
    stmt = select(Article).where(Article.deleted_at.is_(None))
    stmt = stmt.where(Article.status.in_(status)) if status else stmt.where(Article.status != "archived")
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Article.headline.ilike(like), Article.subtitle.ilike(like), Article.slug.ilike(like)))
    if category_id:
        stmt = stmt.where(Article.category_id == category_id)
    if author_id:
        stmt = stmt.where(Article.author_id == author_id)
    order = {
        "updated": [Article.updated_at.desc()], "created": [Article.created_at.desc()],
        "published": [Article.published_at.desc().nulls_last()], "headline": [Article.headline.asc()],
    }[sort]
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = list(db.scalars(stmt.order_by(*order).offset(params.offset).limit(params.page_size)))
    counts = dict(db.execute(select(Article.status, func.count()).where(Article.deleted_at.is_(None)).group_by(Article.status)).all())
    return {"items": _list_out(db, items), "total": total, "page": params.page, "page_size": params.page_size,
            "status_counts": {k: int(v) for k, v in counts.items()}}


@router.post("", response_model=ArticleOut, status_code=201)
def create(body: ArticleIn, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.ARTICLES_WRITE))):
    a = svc.create_article(db, body.model_dump(exclude_unset=True, exclude={"version"}), current)
    record(db, "article.created", f"Draft “{a.headline[:100]}” created", actor=current, entity_type="article", entity_id=a.id)
    db.commit()
    return article_out(db, a, current)


@router.get("/{article_id}", response_model=ArticleOut)
def get(article_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.READ))):
    return article_out(db, get_or_404(db, Article, article_id, "Article"), current)


@router.put("/{article_id}", response_model=ArticleOut)
def update(article_id: uuid.UUID, body: ArticleIn, db: Session = Depends(get_db),
           current: CurrentUser = Depends(require(P.ARTICLES_WRITE))):
    a = get_or_404(db, Article, article_id, "Article")
    if a.status == "archived":
        raise AppError(409, "conflict", "Restore the article to draft before editing")
    if body.version is not None and body.version != a.version:
        raise AppError(409, "version_conflict", "This article was changed elsewhere. Reload to see the latest version.",
                       {"current_version": a.version})
    changed = svc.apply_changes(db, a, body.model_dump(exclude_unset=True, exclude={"version"}), current)
    if changed:
        record(db, "article.updated", f"“{a.headline[:100]}” saved (v{a.version}): {', '.join(changed)[:200]}",
               actor=current, entity_type="article", entity_id=a.id, details={"fields": changed, "version": a.version})
    db.commit()
    return article_out(db, a, current)


@router.delete("/{article_id}")
def delete(article_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.ARTICLES_WRITE))):
    a = get_or_404(db, Article, article_id, "Article")
    if a.status in ("published", "scheduled"):
        raise AppError(409, "conflict", "Unpublish or cancel the schedule before deleting")
    a.deleted_at = utcnow()
    a.slug = f"{a.slug}-deleted-{uuid.uuid4().hex[:6]}"
    record(db, "article.deleted", f"“{a.headline[:100]}” deleted", actor=current, entity_type="article", entity_id=a.id)
    db.commit()
    return {"ok": True}


@router.post("/{article_id}/transition", response_model=ArticleOut)
def transition(article_id: uuid.UUID, body: TransitionIn, db: Session = Depends(get_db),
               current: CurrentUser = Depends(require(P.READ))):
    a = get_or_404(db, Article, article_id, "Article")
    if body.target in ("published", "unpublished", "scheduled"):
        raise AppError(400, "bad_request", "Use the publish/unpublish endpoints to change publication state")
    previous = a.status
    try:
        svc.transition(db, a, body.target, current, scheduled_at=body.scheduled_at)
    except TransitionError as exc:
        raise AppError(409, "invalid_transition", str(exc)) from exc
    record(db, f"article.{body.target}", f"“{a.headline[:100]}” moved from {previous} to {body.target}",
           actor=current, entity_type="article", entity_id=a.id, details={"from": previous, "to": body.target, "version": a.version})
    if body.target == "approved":
        auto_publish_if_enabled(db, a)
    db.commit()
    return article_out(db, a, current)


@router.get("/{article_id}/preview", response_class=HTMLResponse)
def preview(article_id: uuid.UUID, db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.READ))):
    a = get_or_404(db, Article, article_id, "Article")
    return HTMLResponse(preview_document(a))


@router.get("/{article_id}/checks")
def checks(article_id: uuid.UUID, destination_id: uuid.UUID | None = None, db: Session = Depends(get_db),
           _: CurrentUser = Depends(require(P.READ))):
    a = get_or_404(db, Article, article_id, "Article")
    dest = db.get(PublishingDestination, destination_id) if destination_id else None
    policy = get_setting(db, "publishing")
    return publication_checks(a, commercial=dest.is_commercial if dest else True,
                              block_on_markers=policy.get("block_on_template_markers", True))


@router.post("/{article_id}/publish")
def publish(article_id: uuid.UUID, body: PublishIn, db: Session = Depends(get_db),
            current: CurrentUser = Depends(require(P.ARTICLES_PUBLISH))):
    """Queue a publication. Immediate and draft requests are processed right away."""
    a = get_or_404(db, Article, article_id, "Article")
    dest = get_or_404(db, PublishingDestination, body.destination_id, "Destination")
    job, created = request_publication(db, a, dest, "publish", actor=current, mode=body.mode, scheduled_for=body.scheduled_for)
    db.commit()
    if created and body.mode != "scheduled":
        process_job(db, job)
        db.commit()
    db.refresh(job)
    out = JobOut.model_validate(job).model_copy(update={"article_headline": a.headline, "destination_name": dest.name})
    return {"job": out, "duplicate_request": not created, "article": article_out(db, a, current)}


@router.post("/{article_id}/unpublish")
def unpublish(article_id: uuid.UUID, body: UnpublishIn, db: Session = Depends(get_db),
              current: CurrentUser = Depends(require(P.ARTICLES_PUBLISH))):
    a = get_or_404(db, Article, article_id, "Article")
    dest = get_or_404(db, PublishingDestination, body.destination_id, "Destination")
    job, created = request_publication(db, a, dest, "unpublish", actor=current)
    db.commit()
    if created:
        process_job(db, job)
        db.commit()
    db.refresh(job)
    out = JobOut.model_validate(job).model_copy(update={"article_headline": a.headline, "destination_name": dest.name})
    return {"job": out, "duplicate_request": not created, "article": article_out(db, a, current)}


@router.get("/{article_id}/history", response_model=list[AuditOut])
def history(article_id: uuid.UUID, db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.READ))):
    get_or_404(db, Article, article_id, "Article")
    return list(db.scalars(select(AuditLog).where(AuditLog.entity_type == "article", AuditLog.entity_id == str(article_id))
                           .order_by(AuditLog.created_at.desc()).limit(200)))
