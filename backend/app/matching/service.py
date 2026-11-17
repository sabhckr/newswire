"""Glue between the matching engine and the database."""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.geography.gazetteer import Gazetteer, get_gazetteer
from app.matching.engine import AssignmentSpec, GeoFact, ItemView, KeywordSpec, MatchResult, evaluate
from app.models import (
    AssignmentMatch,
    EditorialAssignment,
    NewsGeography,
    NewsItem,
    NewsItemSource,
    Source,
)
from app.services.audit import notify
from app.services.settings import get_setting

log = logging.getLogger(__name__)


def spec_from_assignment(a: EditorialAssignment, gaz: Gazetteer) -> AssignmentSpec:
    return AssignmentSpec(
        id=a.id,
        headline=a.headline,
        keywords=[KeywordSpec(k.term, k.kind, k.weight) for k in a.keywords],
        min_score=a.min_score,
        language=a.language,
        category_id=a.category_id,
        category_name=a.category.name if a.category else None,
        source_ids={s.id for s in a.sources},
        source_names={s.id: s.publisher or s.name for s in a.sources},
        restrict_to_sources=a.restrict_to_sources,
        geo_targets=gaz.expand_targets([g.id for g in a.geographies]),
        geo_names=[g.name for g in a.geographies],
        geo_mode=a.geo_mode,
        start_date=a.start_date,
        end_date=a.end_date,
    )


def build_item_views(db: Session, items: list[NewsItem], gaz: Gazetteer) -> list[ItemView]:
    if not items:
        return []
    ids = [i.id for i in items]
    geo: dict[uuid.UUID, list[GeoFact]] = defaultdict(list)
    for chunk_start in range(0, len(ids), 500):
        chunk = ids[chunk_start : chunk_start + 500]
        for g in db.scalars(select(NewsGeography).where(NewsGeography.news_item_id.in_(chunk))):
            name = gaz.entities[g.entity_id].name if g.entity_id and g.entity_id in gaz.entities else None
            geo[g.news_item_id].append(GeoFact(g.entity_id, g.role, g.confidence, g.original_text, name))
    links: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for chunk_start in range(0, len(ids), 500):
        chunk = ids[chunk_start : chunk_start + 500]
        for nid, sid in db.execute(
            select(NewsItemSource.news_item_id, NewsItemSource.source_id).where(NewsItemSource.news_item_id.in_(chunk))
        ):
            links[nid].add(sid)
    source_ids = {i.source_id for i in items if i.source_id} | {s for v in links.values() for s in v}
    sources = {s.id: s for s in db.scalars(select(Source).where(Source.id.in_(source_ids)))} if source_ids else {}

    views = []
    for i in items:
        sids = set(links.get(i.id, set()))
        if i.source_id:
            sids.add(i.source_id)
        cat_ids = {c.id for sid in sids if sid in sources for c in sources[sid].categories}
        views.append(
            ItemView(
                id=i.id,
                title=i.title,
                body=" ".join(p for p in (i.summary, (i.content or "")[:3000]) if p),
                language=i.language,
                category_text=i.category_text,
                source_ids=sids,
                source_category_ids=cat_ids,
                publisher_names={sid: sources[sid].publisher or sources[sid].name for sid in sids if sid in sources},
                published_at=i.published_at,
                retrieved_at=i.retrieved_at,
                geo=geo.get(i.id, []),
            )
        )
    return views


def _config(db: Session) -> tuple[dict, dict]:
    return get_setting(db, "matching.weights"), get_setting(db, "matching.options")


def match_items(db: Session, items: list[NewsItem], assignments: list[EditorialAssignment] | None = None) -> int:
    """Score new items against active assignments and store matches. Returns matches created."""
    if not items:
        return 0
    if assignments is None:
        assignments = list(db.scalars(select(EditorialAssignment).where(EditorialAssignment.status == "active")))
    if not assignments:
        return 0
    gaz = get_gazetteer(db)
    weights, opts = _config(db)
    views = build_item_views(db, items, gaz)
    now = utcnow()
    created = 0
    for a in assignments:
        spec = spec_from_assignment(a, gaz)
        new_for_assignment = 0
        for view in views:
            result = evaluate(spec, view, weights, opts, gaz, now)
            if result.matched and _store(db, a, view.id, result):
                new_for_assignment += 1
        if new_for_assignment:
            created += new_for_assignment
            a.last_matched_at = now
            a.match_count = db.scalar(
                select(func.count()).select_from(AssignmentMatch).where(AssignmentMatch.assignment_id == a.id)
            ) or 0
            if a.priority in ("high", "urgent"):
                notify(
                    db, "match", f"{new_for_assignment} new match{'es' if new_for_assignment > 1 else ''}: {a.headline[:120]}",
                    link=f"/research/{a.id}",
                )
    db.flush()
    return created


def _store(db: Session, a: EditorialAssignment, item_id: uuid.UUID, result: MatchResult) -> bool:
    existing = db.scalar(
        select(AssignmentMatch).where(AssignmentMatch.assignment_id == a.id, AssignmentMatch.news_item_id == item_id)
    )
    if existing is not None:
        existing.score = result.score
        existing.breakdown = result.breakdown()
        existing.explanation = result.explanation
        return False
    db.add(
        AssignmentMatch(
            assignment_id=a.id,
            news_item_id=item_id,
            score=result.score,
            breakdown=result.breakdown(),
            explanation=result.explanation,
            matched_at=utcnow(),
        )
    )
    return True


def recent_items(db: Session, days: int, limit: int) -> list[NewsItem]:
    since = utcnow() - timedelta(days=days)
    return list(
        db.scalars(
            select(NewsItem).where(NewsItem.sort_at >= since).order_by(NewsItem.sort_at.desc()).limit(limit)
        )
    )


def rematch_assignment(db: Session, a: EditorialAssignment, days: int | None = None) -> dict:
    """Re-score recent items for one assignment after its rules changed.

    Unreviewed matches that no longer qualify are removed; matches an editor has reviewed stay.
    """
    weights, opts = _config(db)
    days = days or int(opts["backfill_days"])
    gaz = get_gazetteer(db)
    spec = spec_from_assignment(a, gaz)
    items = recent_items(db, days, int(opts["backfill_limit"]))
    now = utcnow()
    keep: set[uuid.UUID] = set()
    created = updated = 0
    for start in range(0, len(items), 500):
        views = build_item_views(db, items[start : start + 500], gaz)
        for view in views:
            result = evaluate(spec, view, weights, opts, gaz, now)
            if result.matched:
                keep.add(view.id)
                if _store(db, a, view.id, result):
                    created += 1
                else:
                    updated += 1
    db.flush()
    stale_q = select(AssignmentMatch.id).where(
        AssignmentMatch.assignment_id == a.id,
        AssignmentMatch.review_status == "unreviewed",
    )
    item_ids_in_window = {i.id for i in items}
    stale = [
        mid
        for mid, nid in db.execute(stale_q.add_columns(AssignmentMatch.news_item_id)).all()
        if nid in item_ids_in_window and nid not in keep
    ]
    if stale:
        db.execute(delete(AssignmentMatch).where(AssignmentMatch.id.in_(stale)))
    a.match_count = db.scalar(
        select(func.count()).select_from(AssignmentMatch).where(AssignmentMatch.assignment_id == a.id)
    ) or 0
    if created:
        a.last_matched_at = now
    db.flush()
    return {"scanned": len(items), "created": created, "updated": updated, "removed": len(stale)}


def preview(db: Session, spec: AssignmentSpec, days: int = 7, limit: int = 50) -> dict:
    """Evaluate draft rules against recent items without storing anything."""
    weights, opts = _config(db)
    gaz = get_gazetteer(db)
    items = recent_items(db, days, int(opts["backfill_limit"]))
    by_id = {i.id: i for i in items}
    now = utcnow()
    matched: list[tuple[NewsItem, MatchResult]] = []
    near = 0
    for start in range(0, len(items), 500):
        for view in build_item_views(db, items[start : start + 500], gaz):
            res = evaluate(spec, view, weights, opts, gaz, now)
            if res.matched:
                matched.append((by_id[view.id], res))
            elif res.rejected_reason == "score below threshold":
                near += 1
    matched.sort(key=lambda t: t[1].score, reverse=True)
    return {
        "scanned": len(items),
        "matched": len(matched),
        "below_threshold": near,
        "items": [
            {
                "news_item_id": str(i.id),
                "title": i.title,
                "url": i.url,
                "publisher": (i.source.publisher if i.source else None),
                "published_at": i.published_at.isoformat() if i.published_at else None,
                "score": r.score,
                "explanation": r.explanation,
                "breakdown": r.breakdown(),
            }
            for i, r in matched[:limit]
        ],
    }
