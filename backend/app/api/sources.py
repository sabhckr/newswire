from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Literal

from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.api.common import get_or_404
from app.collectors.ingest import collect_source, probe_source_url
from app.core.db import get_db, utcnow
from app.core.errors import AppError, conflict, not_found
from app.core.http import UnsafeURLError, validate_url
from app.core.pagination import Page, PageParams, paginate
from app.core.text import slugify
from app.models import Category, NewsItem, Source, SourceFetchLog
from app.schemas.common import CategoryIn, CategoryOut
from app.schemas.domain import BulkIn, FetchLogOut, SourceDiscoverIn, SourceIn, SourceOut, SourceTestIn
from app.security import permissions as P
from app.security.deps import CurrentUser, require
from app.services.audit import record
from app.services.source_import import import_entries, list_packs, load_pack

router = APIRouter(tags=["sources"])


def _check_url(url: str) -> None:
    try:
        validate_url(url)
    except UnsafeURLError as exc:
        raise AppError(422, "unsafe_url", str(exc)) from exc


def _apply(db: Session, s: Source, body: SourceIn) -> None:
    data = body.model_dump(exclude={"category_ids"})
    for k, v in data.items():
        setattr(s, k, v)
    cats = list(db.scalars(select(Category).where(Category.id.in_(body.category_ids)))) if body.category_ids else []
    if len(cats) != len(set(body.category_ids)):
        raise not_found("Category")
    s.categories = cats


def _live_duplicate(db: Session, url: str, exclude: uuid.UUID | None = None) -> bool:
    q = select(Source.id).where(Source.feed_url == url, Source.deleted_at.is_(None))
    if exclude:
        q = q.where(Source.id != exclude)
    return db.scalar(q) is not None


# --- categories ----------------------------------------------------------------------------

@router.get("/categories", response_model=list[CategoryOut])
def list_categories(db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.READ))):
    return list(db.scalars(select(Category).order_by(Category.name)))


@router.post("/categories", response_model=CategoryOut, status_code=201)
def create_category(body: CategoryIn, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.SOURCES_WRITE))):
    slug = slugify(body.name)
    if db.scalar(select(Category).where(or_(Category.slug == slug, Category.name == body.name))):
        raise conflict("A category with this name already exists")
    c = Category(name=body.name.strip(), slug=slug, description=body.description)
    db.add(c)
    record(db, "category.created", f"Category {c.name} created", actor=current, entity_type="category")
    db.commit()
    return c


@router.delete("/categories/{category_id}")
def delete_category(category_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.SOURCES_WRITE))):
    c = get_or_404(db, Category, category_id, "Category")
    record(db, "category.deleted", f"Category {c.name} deleted", actor=current, entity_type="category", entity_id=c.id)
    db.delete(c)
    db.commit()
    return {"ok": True}


# --- sources -------------------------------------------------------------------------------

@router.get("/sources", response_model=Page[SourceOut])
def list_sources(
    q: str | None = None,
    status: Literal["active", "disabled", "failing", "healthy", "pending"] | None = None,
    country: str | None = None,
    language: str | None = None,
    category_id: uuid.UUID | None = None,
    source_type: Literal["rss", "atom", "json", "sitemap"] | None = None,
    sort: Literal["name", "publisher", "last_checked", "errors", "priority", "created"] = "name",
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require(P.READ)),
):
    stmt = select(Source).where(Source.deleted_at.is_(None))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Source.name.ilike(like), Source.publisher.ilike(like), Source.feed_url.ilike(like)))
    if status == "active":
        stmt = stmt.where(Source.is_active.is_(True))
    elif status == "disabled":
        stmt = stmt.where(Source.is_active.is_(False))
    elif status == "failing":
        stmt = stmt.where(Source.is_active.is_(True), Source.consecutive_failures > 0)
    elif status == "healthy":
        stmt = stmt.where(Source.is_active.is_(True), Source.consecutive_failures == 0, Source.last_fetched_at.is_not(None))
    elif status == "pending":
        stmt = stmt.where(Source.last_fetched_at.is_(None))
    if country:
        stmt = stmt.where(Source.country_code == country.upper())
    if language:
        stmt = stmt.where(Source.language == language.lower())
    if category_id:
        stmt = stmt.where(Source.categories.any(Category.id == category_id))
    if source_type:
        stmt = stmt.where(Source.source_type == source_type)
    order = {
        "name": [Source.name.asc()],
        "publisher": [Source.publisher.asc(), Source.name.asc()],
        "last_checked": [Source.last_fetched_at.desc().nulls_last()],
        "errors": [Source.consecutive_failures.desc(), Source.name.asc()],
        "priority": [Source.priority.asc(), Source.name.asc()],
        "created": [Source.created_at.desc()],
    }[sort]
    items, total = paginate(db, stmt.order_by(*order), params)
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


@router.post("/sources", response_model=SourceOut, status_code=201)
def create_source(body: SourceIn, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.SOURCES_WRITE))):
    _check_url(body.feed_url)
    if _live_duplicate(db, body.feed_url):
        raise conflict("A source with this feed URL already exists")
    s = Source()
    _apply(db, s, body)
    s.next_fetch_at = utcnow()
    db.add(s)
    db.flush()
    record(db, "source.created", f"Source {s.name} ({s.feed_url}) added", actor=current, entity_type="source", entity_id=s.id)
    db.commit()
    return s


@router.post("/sources/test")
def test_new_source(body: SourceTestIn, _: CurrentUser = Depends(require(P.SOURCES_WRITE))):
    _check_url(body.feed_url)
    return probe_source_url(body.feed_url, body.source_type, body.api_config, body.respect_robots)


@router.post("/sources/discover")
def discover_source_feeds(body: SourceDiscoverIn, _: CurrentUser = Depends(require(P.SOURCES_WRITE))):
    """Look for the feeds and news sitemaps a website advertises, and test each one."""
    _check_url(body.url)
    from app.collectors.discover import discover_feeds

    return discover_feeds(body.url, respect_robots=body.respect_robots)


@router.get("/sources/export")
def export_sources(db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.READ))):
    from app.services.backups import export_config

    data = {"newswire_sources": 1, "sources": export_config(db)["sources"]}
    return JSONResponse(data, headers={"Content-Disposition": 'attachment; filename="newswire-sources.json"'})


@router.post("/sources/import")
async def import_sources(request: Request, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.SOURCES_WRITE))):
    """Import sources from a NEWSWIRE JSON export or an OPML subscription list."""
    raw = await request.body()
    if len(raw) > 5_000_000:
        raise AppError(413, "payload_too_large", "Import file is larger than 5 MB")
    entries: list[dict] = []
    text = raw.decode("utf-8", errors="replace").lstrip()
    if text.startswith("{") or text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AppError(400, "bad_request", f"Invalid JSON: {exc}") from exc
        entries = data.get("sources", []) if isinstance(data, dict) else data
    else:
        soup = BeautifulSoup(text, "html.parser")
        for o in soup.find_all("outline"):
            url = o.get("xmlurl") or o.get("xmlUrl")
            if url:
                title = o.get("title") or o.get("text") or url
                entries.append({"name": title, "publisher": title, "feed_url": url, "website_url": o.get("htmlurl")})
    result = import_entries(db, entries)
    created, skipped, errors = result["created"], result["skipped"], result["errors"]
    record(db, "source.imported", f"Imported {created} sources ({skipped} duplicates skipped, {len(errors)} errors)",
           actor=current, entity_type="source")
    db.commit()
    return {"created": created, "skipped": skipped, "errors": errors}


@router.get("/sources/packs")
def list_source_packs(_: CurrentUser = Depends(require(P.READ))):
    """Bundled lists of public feeds (verified when the pack was built) that can be added in one step."""
    return list_packs()


@router.post("/sources/packs/{pack_id}/import")
def import_source_pack(pack_id: str, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.SOURCES_WRITE))):
    entries = load_pack(pack_id)
    if entries is None:
        raise not_found("Source pack")
    result = import_entries(db, entries)
    record(db, "source.pack_imported",
           f"Added source pack {pack_id}: {result['created']} sources ({result['skipped']} already present, "
           f"{len(result['errors'])} errors)", actor=current, entity_type="source")
    db.commit()
    return result


@router.post("/sources/bulk")
def bulk_sources(body: BulkIn, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.SOURCES_WRITE))):
    if body.action not in ("enable", "disable", "delete", "refresh_soon"):
        raise AppError(400, "bad_request", "Unknown bulk action")
    sources = list(db.scalars(select(Source).where(Source.id.in_(body.ids), Source.deleted_at.is_(None))))
    for s in sources:
        if body.action == "enable":
            s.is_active = True
            s.next_fetch_at = utcnow()
        elif body.action == "disable":
            s.is_active = False
        elif body.action == "delete":
            s.deleted_at = utcnow()
            s.is_active = False
        else:
            s.next_fetch_at = utcnow()
    record(db, f"source.bulk_{body.action}", f"Bulk {body.action} on {len(sources)} sources", actor=current,
           entity_type="source", details={"ids": [str(s.id) for s in sources]})
    db.commit()
    return {"updated": len(sources)}


@router.get("/sources/{source_id}", response_model=SourceOut)
def get_source(source_id: uuid.UUID, db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.READ))):
    return get_or_404(db, Source, source_id, "Source")


@router.put("/sources/{source_id}", response_model=SourceOut)
def update_source(source_id: uuid.UUID, body: SourceIn, db: Session = Depends(get_db),
                  current: CurrentUser = Depends(require(P.SOURCES_WRITE))):
    s = get_or_404(db, Source, source_id, "Source")
    _check_url(body.feed_url)
    if _live_duplicate(db, body.feed_url, exclude=s.id):
        raise conflict("A source with this feed URL already exists")
    url_changed = body.feed_url != s.feed_url
    _apply(db, s, body)
    if url_changed:
        s.etag = None
        s.last_modified = None
        s.consecutive_failures = 0
        s.next_fetch_at = utcnow()
    record(db, "source.updated", f"Source {s.name} updated", actor=current, entity_type="source", entity_id=s.id)
    db.commit()
    return s


@router.delete("/sources/{source_id}")
def delete_source(source_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.SOURCES_WRITE))):
    s = get_or_404(db, Source, source_id, "Source")
    s.deleted_at = utcnow()
    s.is_active = False
    record(db, "source.deleted", f"Source {s.name} deleted (collected items are kept)", actor=current,
           entity_type="source", entity_id=s.id)
    db.commit()
    return {"ok": True}


@router.post("/sources/{source_id}/enable", response_model=SourceOut)
def enable_source(source_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.SOURCES_WRITE))):
    s = get_or_404(db, Source, source_id, "Source")
    s.is_active = True
    s.next_fetch_at = utcnow()
    record(db, "source.enabled", f"Source {s.name} enabled", actor=current, entity_type="source", entity_id=s.id)
    db.commit()
    return s


@router.post("/sources/{source_id}/disable", response_model=SourceOut)
def disable_source(source_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.SOURCES_WRITE))):
    s = get_or_404(db, Source, source_id, "Source")
    s.is_active = False
    record(db, "source.disabled", f"Source {s.name} disabled", actor=current, entity_type="source", entity_id=s.id)
    db.commit()
    return s


@router.post("/sources/{source_id}/test")
def test_source(source_id: uuid.UUID, db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.SOURCES_WRITE))):
    s = get_or_404(db, Source, source_id, "Source")
    return probe_source_url(s.feed_url, s.source_type, s.api_config, s.respect_robots)


@router.post("/sources/{source_id}/refresh")
def refresh_source(source_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.SOURCES_WRITE))):
    """Fetch this source now (synchronously) and return the fetch log."""
    s = get_or_404(db, Source, source_id, "Source")
    result = collect_source(db, s, trigger="manual")
    record(db, "source.refreshed", f"Manual refresh of {s.name}: {result.log.status}, {result.log.items_new} new",
           actor=current, entity_type="source", entity_id=s.id)
    db.commit()
    return {"log": FetchLogOut.model_validate(result.log).model_dump(mode="json"),
            "source": SourceOut.model_validate(s).model_dump(mode="json")}


@router.get("/sources/{source_id}/logs", response_model=Page[FetchLogOut])
def source_logs(source_id: uuid.UUID, status: str | None = None, params: PageParams = Depends(),
                db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.READ))):
    get_or_404(db, Source, source_id, "Source")
    stmt = select(SourceFetchLog).where(SourceFetchLog.source_id == source_id)
    if status:
        stmt = stmt.where(SourceFetchLog.status == status)
    items, total = paginate(db, stmt.order_by(SourceFetchLog.started_at.desc()), params)
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


@router.get("/sources/{source_id}/health")
def source_health(source_id: uuid.UUID, db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.READ))):
    s = get_or_404(db, Source, source_id, "Source")
    now = utcnow()
    out = {"source_id": str(s.id), "health": s.health, "windows": {}}
    for label, delta in (("24h", timedelta(hours=24)), ("7d", timedelta(days=7)), ("30d", timedelta(days=30))):
        row = db.execute(
            select(
                func.count(SourceFetchLog.id),
                func.sum(case((SourceFetchLog.status.in_(("success", "not_modified")), 1), else_=0)),
                func.sum(case((SourceFetchLog.status == "not_modified", 1), else_=0)),
                func.avg(SourceFetchLog.duration_ms),
                func.sum(SourceFetchLog.items_new),
            ).where(SourceFetchLog.source_id == s.id, SourceFetchLog.started_at >= now - delta)
        ).one()
        fetches = int(row[0] or 0)
        out["windows"][label] = {
            "fetches": fetches,
            "successes": int(row[1] or 0),
            "not_modified": int(row[2] or 0),
            "success_rate": round((row[1] or 0) / fetches, 3) if fetches else None,
            "avg_duration_ms": int(row[3]) if row[3] is not None else None,
            "new_items": int(row[4] or 0),
        }
    out["items_total"] = db.scalar(select(func.count()).select_from(NewsItem).where(NewsItem.source_id == s.id)) or 0
    out["latest_item_at"] = db.scalar(select(func.max(NewsItem.retrieved_at)).where(NewsItem.source_id == s.id))
    return out
