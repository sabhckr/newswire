"""Article editing workflow."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.articles.content import sanitize_html, template_html, word_count
from app.articles.state import EDIT_RESETS_APPROVAL, check_transition
from app.core.db import utcnow
from app.core.errors import AppError, conflict, not_found
from app.core.text import slugify
from app.models import (
    Article,
    ArticleImage,
    ArticleSource,
    AssignmentMatch,
    Category,
    EditorialAssignment,
    ImageAsset,
    NewsItem,
    PublishingJob,
    StoryCluster,
    StoryClusterItem,
    Tag,
    VideoAsset,
)
from app.models.articles import article_assignments
from app.security import permissions as P
from app.services.audit import record

CONTENT_FIELDS = {"headline", "subtitle", "body_html", "body_json", "featured_image_id", "featured_image_caption",
                  "video_id"}


def unique_slug(db: Session, base: str, exclude_id: uuid.UUID | None = None) -> str:
    base = slugify(base)[:200] or "article"
    slug = base
    n = 2
    while True:
        q = select(Article.id).where(Article.slug == slug)
        if exclude_id is not None:
            q = q.where(Article.id != exclude_id)
        if db.scalar(q) is None:
            return slug
        slug = f"{base}-{n}"
        n += 1


def _tags(db: Session, names: list[str]) -> list[Tag]:
    out = []
    for name in names:
        name = name.strip()[:80]
        if not name:
            continue
        slug = slugify(name)
        tag = db.scalar(select(Tag).where(Tag.slug == slug))
        if tag is None:
            tag = Tag(name=name, slug=slug)
            db.add(tag)
            db.flush()
        if tag not in out:
            out.append(tag)
    return out


def apply_changes(db: Session, article: Article, data: dict[str, Any], current) -> list[str]:
    """Apply editable fields. Returns the list of changed field names."""
    changed: list[str] = []
    for field in ("headline", "subtitle", "excerpt", "seo_title", "seo_description", "featured_image_caption", "body_json"):
        if field in data and getattr(article, field) != data[field]:
            setattr(article, field, data[field])
            changed.append(field)
    if "body_html" in data:
        clean = sanitize_html(data["body_html"])
        if clean != article.body_html:
            article.body_html = clean
            article.word_count = word_count(clean)
            changed.append("body_html")
    if "slug" in data and data["slug"]:
        slug = slugify(data["slug"])
        if slug != article.slug:
            if db.scalar(select(Article.id).where(Article.slug == slug, Article.id != article.id)):
                raise conflict(f"The slug '{slug}' is already used by another article")
            article.slug = slug
            changed.append("slug")
    if "category_id" in data:
        cid = data["category_id"]
        if cid and db.get(Category, cid) is None:
            raise not_found("Category")
        if article.category_id != cid:
            article.category_id = cid
            changed.append("category_id")
    if "featured_image_id" in data:
        fid = data["featured_image_id"]
        if fid and db.get(ImageAsset, fid) is None:
            raise not_found("Image")
        if article.featured_image_id != fid:
            article.featured_image_id = fid
            article.featured_image = db.get(ImageAsset, fid) if fid else None
            changed.append("featured_image_id")
    if "video_id" in data:
        vid = data["video_id"]
        if vid and db.get(VideoAsset, vid) is None:
            raise not_found("Video")
        if article.video_id != vid:
            article.video_id = vid
            article.video = db.get(VideoAsset, vid) if vid else None
            changed.append("video_id")
    if "geo_entity_ids" in data:
        article.geo_entity_ids = [str(x) for x in (data["geo_entity_ids"] or [])]
        changed.append("geo_entity_ids")
    if "tags" in data:
        article.tags = _tags(db, data["tags"] or [])
        changed.append("tags")
    if "sources" in data:
        article.sources.clear()
        db.flush()
        for pos, s in enumerate(data["sources"] or []):
            nid = s.get("news_item_id")
            item = db.get(NewsItem, nid) if nid else None
            url = s.get("url") or (item.url if item else None)
            if not url:
                continue
            article.sources.append(ArticleSource(
                news_item_id=item.id if item else None, url=url,
                title=s.get("title") or (item.title if item else None),
                publisher=s.get("publisher") or (item.source.publisher if item and item.source else None),
                note=s.get("note"), position=pos,
            ))
        changed.append("sources")
    if "images" in data:
        article.images.clear()
        db.flush()
        for pos, im in enumerate(data["images"] or []):
            asset = db.get(ImageAsset, im["image_asset_id"])
            if asset is None:
                raise not_found("Image")
            article.images.append(ArticleImage(image_asset_id=asset.id, image=asset, caption=im.get("caption"),
                                               position=pos, is_modified=bool(im.get("is_modified"))))
        changed.append("images")
    if "assignment_ids" in data:
        db.execute(article_assignments.delete().where(article_assignments.c.article_id == article.id))
        for aid in data["assignment_ids"] or []:
            if db.get(EditorialAssignment, aid) is None:
                raise not_found("Assignment")
            db.execute(article_assignments.insert().values(article_id=article.id, assignment_id=aid))
        changed.append("assignment_ids")

    if changed:
        article.version += 1
        content_changed = bool(CONTENT_FIELDS & set(changed)) or "images" in changed
        if content_changed and article.status in EDIT_RESETS_APPROVAL and not current.can(P.ARTICLES_APPROVE):
            cancel_scheduled_jobs(db, article, current, "content edited, so the article needs approval again")
            article.status = "in_review"
            article.approved_at = None
            article.approved_by_id = None
            changed.append("status→in_review")
    db.flush()
    return changed


def create_article(db: Session, data: dict[str, Any], current) -> Article:
    headline = (data.get("headline") or "").strip()
    if not headline:
        raise AppError(422, "validation_error", "Headline is required")
    article = Article(headline=headline, slug=unique_slug(db, data.get("slug") or headline),
                      author_id=current.id, status="draft", body_html="", version=0)
    db.add(article)
    db.flush()
    rest = {k: v for k, v in data.items() if k not in ("headline", "slug")}
    apply_changes(db, article, rest, current)
    article.version = 1
    db.flush()
    return article


def cancel_scheduled_jobs(db: Session, article: Article, current, reason: str) -> None:
    """Leaving the scheduled state must stop the queued publication, or it would still fire later."""
    now = utcnow()
    for job in db.scalars(select(PublishingJob).where(
        PublishingJob.article_id == article.id, PublishingJob.status == "pending",
        PublishingJob.action == "publish", PublishingJob.scheduled_for.is_not(None),
    )):
        job.status = "cancelled"
        job.completed_at = now
        job.last_error = f"Cancelled: {reason}"
        record(db, "publishing.cancelled", f"Scheduled publication of \u201c{article.headline[:80]}\u201d cancelled: {reason}",
               actor=current, entity_type="article", entity_id=article.id, details={"job_id": str(job.id)})
    article.scheduled_at = None


def transition(db: Session, article: Article, target: str, current, *, scheduled_at=None) -> Article:
    check_transition(article.status, target, current.permissions)
    now = utcnow()
    if target == "approved":
        article.approved_at = now
        article.approved_by_id = current.id
    if target == "scheduled":
        if scheduled_at is None or scheduled_at <= now:
            raise AppError(422, "validation_error", "A future publication time is required")
        article.scheduled_at = scheduled_at
    if target in ("draft", "in_review"):
        article.approved_at = None
        article.approved_by_id = None
    # Leaving the approved states stops every queued scheduled publication (a published article can
    # still have a Facebook post or YouTube upload scheduled for later).
    if (article.status == "scheduled" and target != "published") or target in ("draft", "in_review", "archived"):
        cancel_scheduled_jobs(db, article, current, f"article moved from {article.status} to {target}")
    article.status = target
    db.flush()
    return article


def assignment_ids_for(db: Session, article_id: uuid.UUID) -> list[uuid.UUID]:
    return list(db.scalars(select(article_assignments.c.assignment_id).where(article_assignments.c.article_id == article_id)))


def create_from_assignment(db: Session, assignment: EditorialAssignment, current) -> Article:
    """Draft scaffold: editor's headline, verified references, related coverage, approved images."""
    verified = list(db.scalars(
        select(AssignmentMatch)
        .where(AssignmentMatch.assignment_id == assignment.id, AssignmentMatch.review_status == "verified")
        .order_by(AssignmentMatch.score.desc())
        .limit(15)
    ))
    refs = [(m.news_item.title, m.news_item.url, m.news_item.source.publisher if m.news_item.source else None) for m in verified]
    item_ids = [m.news_item_id for m in verified]
    related: list[str] = []
    if item_ids:
        rows = db.execute(
            select(StoryCluster.title, StoryCluster.publisher_count)
            .join(StoryClusterItem, StoryClusterItem.cluster_id == StoryCluster.id)
            .where(StoryClusterItem.news_item_id.in_(item_ids))
            .distinct()
        ).all()
        related = [f"{t} ({n} publishers)" for t, n in rows][:10]
    images = list(db.scalars(
        select(ImageAsset).where(ImageAsset.assignment_id == assignment.id, ImageAsset.approval_status == "approved")
    ))
    data: dict[str, Any] = {
        "body_html": template_html(assignment, refs, related),
        "category_id": assignment.category_id,
        "geo_entity_ids": [g.id for g in assignment.geographies],
        "sources": [{"news_item_id": m.news_item_id} for m in verified],
        "assignment_ids": [assignment.id],
    }
    if images:
        data["featured_image_id"] = images[0].id
        data["images"] = [{"image_asset_id": i.id} for i in images[1:]]
    return create_article(db, {"headline": assignment.headline, **data}, current)
