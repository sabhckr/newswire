"""Small helpers shared by routers."""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Iterable
from typing import Any

from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import not_found
from app.models import AssignmentMatch, NewsGeography, NewsItem, StoryClusterItem
from app.schemas.domain import NewsItemOut


def get_or_404(db: Session, model, obj_id: Any, label: str | None = None):
    obj = db.get(model, obj_id)
    if obj is None or getattr(obj, "deleted_at", None) is not None:
        raise not_found(label or model.__name__)
    return obj


def csv_response(rows: Iterable[dict], filename: str) -> StreamingResponse:
    rows = list(rows)
    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({k: ("" if v is None else v) for k, v in r.items()})
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def news_out(db: Session, items: list[NewsItem]) -> list[NewsItemOut]:
    """Serialise news items with match counts, cluster ids and confident countries in 3 queries."""
    if not items:
        return []
    ids = [i.id for i in items]
    counts = dict(
        db.execute(
            select(AssignmentMatch.news_item_id, func.count())
            .where(AssignmentMatch.news_item_id.in_(ids))
            .group_by(AssignmentMatch.news_item_id)
        ).all()
    )
    clusters = dict(
        db.execute(select(StoryClusterItem.news_item_id, StoryClusterItem.cluster_id).where(StoryClusterItem.news_item_id.in_(ids))).all()
    )
    countries: dict[uuid.UUID, set[str]] = {}
    for nid, cc in db.execute(
        select(NewsGeography.news_item_id, NewsGeography.country_code).where(
            NewsGeography.news_item_id.in_(ids),
            NewsGeography.role.in_(("mentioned", "event", "article", "editor")),
            NewsGeography.confidence >= 0.5,
            NewsGeography.country_code.is_not(None),
        )
    ):
        countries.setdefault(nid, set()).add(cc)
    out = []
    for i in items:
        o = NewsItemOut.model_validate(i)
        o.publisher = i.source.publisher if i.source else None
        o.source_name = i.source.name if i.source else None
        o.match_count = int(counts.get(i.id, 0))
        o.cluster_id = clusters.get(i.id)
        o.countries = sorted(countries.get(i.id, set()))
        out.append(o)
    return out
