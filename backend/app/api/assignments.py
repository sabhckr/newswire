from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.common import get_or_404, news_out
from app.core.db import get_db, utcnow
from app.core.errors import AppError, not_found
from app.core.pagination import Page, PageParams
from app.geography.gazetteer import get_gazetteer
from app.matching.engine import AssignmentSpec, KeywordSpec
from app.matching.service import preview, rematch_assignment
from app.models import (
    AssignmentKeyword,
    AssignmentMatch,
    Category,
    EditorialAssignment,
    GeographicEntity,
    NewsGeography,
    NewsItem,
    Source,
)
from app.schemas.domain import AssignmentIn, AssignmentOut, BulkIn, MatchBulkIn, MatchOut, MatchReviewIn
from app.security import permissions as P
from app.security.deps import CurrentUser, require
from app.services.audit import record

router = APIRouter(prefix="/assignments", tags=["assignments"])


def _apply(db: Session, a: EditorialAssignment, body: AssignmentIn) -> None:
    for field in ("headline", "description", "category_id", "language", "priority", "status", "start_date", "end_date",
                  "monitoring_interval_seconds", "min_score", "restrict_to_sources", "geo_mode", "city"):
        setattr(a, field, getattr(body, field))
    if body.category_id and db.get(Category, body.category_id) is None:
        raise not_found("Category")
    a.category = db.get(Category, body.category_id) if body.category_id else None
    seen = set()
    keywords = []
    for pos, k in enumerate(body.keywords):
        key = (k.term.strip().lower(), k.kind)
        if not k.term.strip() or key in seen:
            continue
        seen.add(key)
        keywords.append(AssignmentKeyword(term=k.term.strip(), kind=k.kind, weight=k.weight, position=pos))
    if not any(k.kind in ("primary", "synonym", "required") for k in keywords):
        raise AppError(422, "validation_error", "Add at least one primary keyword, synonym or required phrase")
    a.keywords = keywords
    sources = list(db.scalars(select(Source).where(Source.id.in_(body.source_ids), Source.deleted_at.is_(None)))) if body.source_ids else []
    if len(sources) != len(set(body.source_ids)):
        raise not_found("Source")
    a.sources = sources
    geos = list(db.scalars(select(GeographicEntity).where(GeographicEntity.id.in_(body.geography_ids)))) if body.geography_ids else []
    if len(geos) != len(set(body.geography_ids)):
        raise not_found("Place")
    a.geographies = geos
    if body.geo_mode == "require" and not geos:
        raise AppError(422, "validation_error", "Choose at least one place when location is required")


def _out(db: Session, items: list[EditorialAssignment]) -> list[AssignmentOut]:
    ids = [a.id for a in items]
    unreviewed = dict(db.execute(
        select(AssignmentMatch.assignment_id, func.count()).where(
            AssignmentMatch.assignment_id.in_(ids), AssignmentMatch.review_status == "unreviewed")
        .group_by(AssignmentMatch.assignment_id)).all()) if ids else {}
    out = []
    for a in items:
        o = AssignmentOut.model_validate(a)
        o.unreviewed_count = int(unreviewed.get(a.id, 0))
        out.append(o)
    return out


def spec_from_body(db: Session, body: AssignmentIn) -> AssignmentSpec:
    gaz = get_gazetteer(db)
    cat = db.get(Category, body.category_id) if body.category_id else None
    sources = list(db.scalars(select(Source).where(Source.id.in_(body.source_ids)))) if body.source_ids else []
    geos = list(db.scalars(select(GeographicEntity).where(GeographicEntity.id.in_(body.geography_ids)))) if body.geography_ids else []
    return AssignmentSpec(
        id=None, headline=body.headline, keywords=[KeywordSpec(k.term, k.kind, k.weight) for k in body.keywords],
        min_score=body.min_score, language=body.language, category_id=body.category_id,
        category_name=cat.name if cat else None, source_ids={s.id for s in sources},
        source_names={s.id: s.publisher for s in sources}, restrict_to_sources=body.restrict_to_sources,
        geo_targets=gaz.expand_targets([g.id for g in geos]), geo_names=[g.name for g in geos], geo_mode=body.geo_mode,
        start_date=body.start_date, end_date=body.end_date,
    )


@router.get("", response_model=Page[AssignmentOut])
def list_assignments(
    q: str | None = None,
    status: list[Literal["active", "paused", "archived"]] | None = Query(None),
    priority: Literal["low", "normal", "high", "urgent"] | None = None,
    sort: Literal["updated", "priority", "matches", "last_matched", "headline", "created"] = "updated",
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require(P.READ)),
):
    stmt = select(EditorialAssignment)
    stmt = stmt.where(EditorialAssignment.status.in_(status)) if status else stmt.where(EditorialAssignment.status != "archived")
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(EditorialAssignment.headline.ilike(like), EditorialAssignment.description.ilike(like),
                              EditorialAssignment.keywords.any(AssignmentKeyword.term.ilike(like))))
    if priority:
        stmt = stmt.where(EditorialAssignment.priority == priority)
    from sqlalchemy import case

    prio = case({"urgent": 0, "high": 1, "normal": 2, "low": 3}, value=EditorialAssignment.priority, else_=4)
    order = {
        "updated": [EditorialAssignment.updated_at.desc()],
        "priority": [prio, EditorialAssignment.updated_at.desc()],
        "matches": [EditorialAssignment.match_count.desc()],
        "last_matched": [EditorialAssignment.last_matched_at.desc().nulls_last()],
        "headline": [EditorialAssignment.headline.asc()],
        "created": [EditorialAssignment.created_at.desc()],
    }[sort]
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = list(db.scalars(stmt.order_by(*order).offset(params.offset).limit(params.page_size)))
    return Page(items=_out(db, items), total=total, page=params.page, page_size=params.page_size)


@router.post("", response_model=AssignmentOut, status_code=201)
def create_assignment(body: AssignmentIn, backfill: bool = True, db: Session = Depends(get_db),
                      current: CurrentUser = Depends(require(P.ASSIGNMENTS_WRITE))):
    a = EditorialAssignment(created_by_id=current.id)
    _apply(db, a, body)
    db.add(a)
    db.flush()
    stats = rematch_assignment(db, a) if backfill and a.status == "active" else None
    record(db, "assignment.created", f"Assignment “{a.headline[:120]}” created"
           + (f"; {stats['created']} matches in the last {stats['scanned']} reports" if stats else ""),
           actor=current, entity_type="assignment", entity_id=a.id)
    db.commit()
    return _out(db, [a])[0]


@router.post("/preview")
def preview_assignment(body: AssignmentIn, days: int = Query(7, ge=1, le=60), db: Session = Depends(get_db),
                       _: CurrentUser = Depends(require(P.READ))):
    """Score recent reports against unsaved rules (nothing is stored)."""
    return preview(db, spec_from_body(db, body), days=days)


@router.post("/bulk")
def bulk_assignments(body: BulkIn, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.ASSIGNMENTS_WRITE))):
    if body.action not in ("pause", "resume", "archive", "delete"):
        raise AppError(400, "bad_request", "Unknown bulk action")
    items = list(db.scalars(select(EditorialAssignment).where(EditorialAssignment.id.in_(body.ids))))
    for a in items:
        if body.action == "delete":
            db.delete(a)
        else:
            a.status = {"pause": "paused", "resume": "active", "archive": "archived"}[body.action]
    record(db, f"assignment.bulk_{body.action}", f"Bulk {body.action} on {len(items)} assignments", actor=current,
           entity_type="assignment", details={"ids": [str(a.id) for a in items]})
    db.commit()
    return {"updated": len(items)}


@router.get("/{assignment_id}", response_model=AssignmentOut)
def get_assignment(assignment_id: uuid.UUID, db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.READ))):
    return _out(db, [get_or_404(db, EditorialAssignment, assignment_id, "Assignment")])[0]


@router.put("/{assignment_id}", response_model=AssignmentOut)
def update_assignment(assignment_id: uuid.UUID, body: AssignmentIn, rematch: bool = True, db: Session = Depends(get_db),
                      current: CurrentUser = Depends(require(P.ASSIGNMENTS_WRITE))):
    a = get_or_404(db, EditorialAssignment, assignment_id, "Assignment")
    keep_archived = a.status == "archived"
    _apply(db, a, body)
    if keep_archived:
        a.status = "archived"
    db.flush()
    stats = rematch_assignment(db, a) if rematch and a.status == "active" else None
    record(db, "assignment.updated", f"Assignment “{a.headline[:120]}” updated", actor=current,
           entity_type="assignment", entity_id=a.id, details={"rematch": stats})
    db.commit()
    return _out(db, [a])[0]


@router.delete("/{assignment_id}")
def delete_assignment(assignment_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.ASSIGNMENTS_WRITE))):
    a = get_or_404(db, EditorialAssignment, assignment_id, "Assignment")
    record(db, "assignment.deleted", f"Assignment “{a.headline[:120]}” deleted with its matches", actor=current,
           entity_type="assignment", entity_id=a.id)
    db.delete(a)
    db.commit()
    return {"ok": True}


@router.post("/{assignment_id}/duplicate", response_model=AssignmentOut, status_code=201)
def duplicate_assignment(assignment_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.ASSIGNMENTS_WRITE))):
    src = get_or_404(db, EditorialAssignment, assignment_id, "Assignment")
    a = EditorialAssignment(
        headline=f"{src.headline} (copy)"[:500], description=src.description, category_id=src.category_id,
        language=src.language, priority=src.priority, status="paused", start_date=src.start_date, end_date=src.end_date,
        monitoring_interval_seconds=src.monitoring_interval_seconds, min_score=src.min_score,
        restrict_to_sources=src.restrict_to_sources, geo_mode=src.geo_mode, city=src.city, created_by_id=current.id,
        keywords=[AssignmentKeyword(term=k.term, kind=k.kind, weight=k.weight, position=k.position) for k in src.keywords],
        sources=list(src.sources), geographies=list(src.geographies),
    )
    db.add(a)
    db.flush()
    record(db, "assignment.duplicated", f"Assignment duplicated from “{src.headline[:100]}” (paused)", actor=current,
           entity_type="assignment", entity_id=a.id)
    db.commit()
    return _out(db, [a])[0]


class _StatusAction(BaseModel):
    pass


def _set_status(db, assignment_id, status, current, verb):
    a = get_or_404(db, EditorialAssignment, assignment_id, "Assignment")
    a.status = status
    record(db, f"assignment.{verb}", f"Assignment “{a.headline[:120]}” {verb}", actor=current,
           entity_type="assignment", entity_id=a.id)
    db.commit()
    return _out(db, [a])[0]


@router.post("/{assignment_id}/pause", response_model=AssignmentOut)
def pause(assignment_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.ASSIGNMENTS_WRITE))):
    return _set_status(db, assignment_id, "paused", current, "paused")


@router.post("/{assignment_id}/resume", response_model=AssignmentOut)
def resume(assignment_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.ASSIGNMENTS_WRITE))):
    return _set_status(db, assignment_id, "active", current, "resumed")


@router.post("/{assignment_id}/archive", response_model=AssignmentOut)
def archive(assignment_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.ASSIGNMENTS_WRITE))):
    return _set_status(db, assignment_id, "archived", current, "archived")


@router.post("/{assignment_id}/rematch")
def rematch(assignment_id: uuid.UUID, days: int = Query(7, ge=1, le=90), db: Session = Depends(get_db),
            current: CurrentUser = Depends(require(P.ASSIGNMENTS_WRITE))):
    a = get_or_404(db, EditorialAssignment, assignment_id, "Assignment")
    stats = rematch_assignment(db, a, days)
    record(db, "assignment.rematched", f"Re-scored {stats['scanned']} reports for “{a.headline[:100]}”: "
           f"{stats['created']} new, {stats['removed']} removed", actor=current, entity_type="assignment", entity_id=a.id)
    db.commit()
    return stats


@router.get("/{assignment_id}/matches")
def list_matches(
    assignment_id: uuid.UUID,
    review_status: list[Literal["unreviewed", "verified", "rejected", "archived"]] | None = Query(None),
    sort: Literal["relevance", "latest", "oldest"] = "relevance",
    publisher: str | None = None,
    country: str | None = None,
    language: str | None = None,
    duplicates: Literal["include", "exclude", "only"] = "include",
    min_score: float | None = None,
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require(P.READ)),
):
    get_or_404(db, EditorialAssignment, assignment_id, "Assignment")
    stmt = select(AssignmentMatch).join(NewsItem, NewsItem.id == AssignmentMatch.news_item_id).where(
        AssignmentMatch.assignment_id == assignment_id)
    if review_status:
        stmt = stmt.where(AssignmentMatch.review_status.in_(review_status))
    else:
        stmt = stmt.where(AssignmentMatch.review_status != "archived")
    if publisher:
        stmt = stmt.where(NewsItem.source_id.in_(select(Source.id).where(Source.publisher == publisher)))
    if country:
        stmt = stmt.where(NewsItem.id.in_(select(NewsGeography.news_item_id).where(
            NewsGeography.country_code == country.upper(), NewsGeography.confidence >= 0.5,
            NewsGeography.role != "publisher")))
    if language:
        stmt = stmt.where(NewsItem.language.ilike(f"{language}%"))
    if duplicates == "exclude":
        stmt = stmt.where(NewsItem.is_duplicate.is_(False))
    elif duplicates == "only":
        stmt = stmt.where(NewsItem.is_duplicate.is_(True))
    if min_score is not None:
        stmt = stmt.where(AssignmentMatch.score >= min_score)
    order = {
        "relevance": [AssignmentMatch.score.desc(), NewsItem.sort_at.desc()],
        "latest": [NewsItem.sort_at.desc()],
        "oldest": [NewsItem.sort_at.asc()],
    }[sort]
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(db.scalars(stmt.order_by(*order).offset(params.offset).limit(params.page_size)))
    items_out = {o.id: o for o in news_out(db, [m.news_item for m in rows])}
    out = []
    for m in rows:
        mo = MatchOut.model_validate(m)
        mo.news_item = items_out[m.news_item_id]
        out.append(mo)
    return {"items": out, "total": total, "page": params.page, "page_size": params.page_size}


@router.patch("/matches/{match_id}", response_model=MatchOut)
def review_match(match_id: uuid.UUID, body: MatchReviewIn, db: Session = Depends(get_db),
                 current: CurrentUser = Depends(require(P.RESEARCH_WRITE))):
    m = get_or_404(db, AssignmentMatch, match_id, "Match")
    if body.review_status is not None and body.review_status != m.review_status:
        m.review_status = body.review_status
        m.reviewed_by_id = current.id
        m.reviewed_at = utcnow()
        record(db, "match.reviewed", f"Match marked {body.review_status}: {m.news_item.title[:100]}", actor=current,
               entity_type="assignment", entity_id=m.assignment_id, details={"match_id": str(m.id)})
    if body.notes is not None:
        m.notes = body.notes
    db.commit()
    mo = MatchOut.model_validate(m)
    mo.news_item = news_out(db, [m.news_item])[0]
    return mo


@router.post("/matches/bulk")
def bulk_review(body: MatchBulkIn, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.RESEARCH_WRITE))):
    matches = list(db.scalars(select(AssignmentMatch).where(AssignmentMatch.id.in_(body.ids))))
    now = utcnow()
    for m in matches:
        m.review_status = body.review_status
        m.reviewed_by_id = current.id
        m.reviewed_at = now
    if matches:
        record(db, "match.bulk_reviewed", f"{len(matches)} matches marked {body.review_status}", actor=current,
               entity_type="assignment", entity_id=matches[0].assignment_id)
    db.commit()
    return {"updated": len(matches)}
