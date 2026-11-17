from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.common import get_or_404, news_out
from app.clustering.engine import add_item, merge_clusters, refresh_cluster, remove_item, split_cluster
from app.core.db import get_db
from app.core.errors import AppError
from app.core.pagination import PageParams
from app.models import AssignmentMatch, EditorialAssignment, NewsGeography, NewsItem, Source, StoryCluster, StoryClusterItem
from app.schemas.domain import ClusterItemIn, ClusterMergeIn, ClusterOut, ClusterSplitIn, ClusterUpdateIn
from app.security import permissions as P
from app.security.deps import CurrentUser, require
from app.services.audit import record

router = APIRouter(prefix="/clusters", tags=["clusters"])


def _publishers(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    out: dict[uuid.UUID, list[str]] = {}
    if not ids:
        return out
    rows = db.execute(
        select(StoryClusterItem.cluster_id, Source.publisher)
        .join(NewsItem, NewsItem.id == StoryClusterItem.news_item_id)
        .join(Source, Source.id == NewsItem.source_id)
        .where(StoryClusterItem.cluster_id.in_(ids)).distinct()
    ).all()
    for cid, pub in rows:
        out.setdefault(cid, []).append(pub)
    return out


def _out(db: Session, clusters: list[StoryCluster]) -> list[ClusterOut]:
    pubs = _publishers(db, [c.id for c in clusters])
    res = []
    for c in clusters:
        o = ClusterOut.model_validate(c)
        o.publishers = sorted(pubs.get(c.id, []))
        res.append(o)
    return res


@router.get("")
def list_clusters(
    q: str | None = None,
    status: Literal["active", "archived"] = "active",
    min_publishers: int = 1,
    assignment_id: uuid.UUID | None = None,
    sort: Literal["latest", "largest", "publishers", "oldest"] = "latest",
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require(P.READ)),
):
    stmt = select(StoryCluster).where(StoryCluster.status == status, StoryCluster.publisher_count >= min_publishers)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(StoryCluster.title.ilike(like), StoryCluster.id.in_(
            select(StoryClusterItem.cluster_id).join(NewsItem, NewsItem.id == StoryClusterItem.news_item_id)
            .where(NewsItem.title.ilike(like)))))
    if assignment_id:
        stmt = stmt.where(StoryCluster.id.in_(
            select(StoryClusterItem.cluster_id).join(AssignmentMatch, AssignmentMatch.news_item_id == StoryClusterItem.news_item_id)
            .where(AssignmentMatch.assignment_id == assignment_id)))
    order = {
        "latest": [StoryCluster.last_seen_at.desc()],
        "largest": [StoryCluster.item_count.desc(), StoryCluster.last_seen_at.desc()],
        "publishers": [StoryCluster.publisher_count.desc(), StoryCluster.last_seen_at.desc()],
        "oldest": [StoryCluster.first_seen_at.asc()],
    }[sort]
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = list(db.scalars(stmt.order_by(*order).offset(params.offset).limit(params.page_size)))
    return {"items": _out(db, items), "total": total, "page": params.page, "page_size": params.page_size}


@router.get("/{cluster_id}")
def get_cluster(cluster_id: uuid.UUID, db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.READ))):
    c = get_or_404(db, StoryCluster, cluster_id, "Cluster")
    links = {link.news_item_id: link for link in c.items}
    items = sorted([link.news_item for link in c.items], key=lambda i: i.sort_at)
    item_ids = [i.id for i in items]
    assignments = db.execute(
        select(EditorialAssignment.id, EditorialAssignment.headline, func.count(AssignmentMatch.id), func.max(AssignmentMatch.score))
        .join(AssignmentMatch, AssignmentMatch.assignment_id == EditorialAssignment.id)
        .where(AssignmentMatch.news_item_id.in_(item_ids))
        .group_by(EditorialAssignment.id, EditorialAssignment.headline)
    ).all() if item_ids else []
    geo = db.execute(
        select(NewsGeography.country_code, NewsGeography.city, NewsGeography.role, func.count(func.distinct(NewsGeography.news_item_id)))
        .where(NewsGeography.news_item_id.in_(item_ids), NewsGeography.confidence >= 0.5, NewsGeography.role != "publisher")
        .group_by(NewsGeography.country_code, NewsGeography.city, NewsGeography.role)
    ).all() if item_ids else []
    serialised = []
    for o in news_out(db, items):
        d = o.model_dump(mode="json")
        link = links[o.id]
        d.update(cluster_method=link.method, cluster_similarity=link.similarity, added_at=link.added_at)
        serialised.append(d)
    return {
        **_out(db, [c])[0].model_dump(mode="json"),
        "items": serialised,
        "assignments": [{"id": str(r[0]), "headline": r[1], "matches": int(r[2]), "top_score": r[3]} for r in assignments],
        "geography": [{"country_code": r[0], "city": r[1], "role": r[2], "items": int(r[3])} for r in geo],
    }


@router.patch("/{cluster_id}")
def update_cluster(cluster_id: uuid.UUID, body: ClusterUpdateIn, db: Session = Depends(get_db),
                   current: CurrentUser = Depends(require(P.CLUSTERS_WRITE))):
    c = get_or_404(db, StoryCluster, cluster_id, "Cluster")
    if body.reset_title:
        c.title_source = "source_headline"
        refresh_cluster(db, c)
    elif body.title is not None:
        c.title = body.title
        c.title_source = "editor"
    if body.notes is not None:
        c.notes = body.notes
    if body.is_locked is not None:
        c.is_locked = body.is_locked
    if body.status is not None:
        c.status = body.status
    record(db, "cluster.updated", f"Cluster “{c.title[:100]}” updated", actor=current, entity_type="cluster", entity_id=c.id)
    db.commit()
    return _out(db, [c])[0]


@router.post("/{cluster_id}/merge")
def merge(cluster_id: uuid.UUID, body: ClusterMergeIn, db: Session = Depends(get_db),
          current: CurrentUser = Depends(require(P.CLUSTERS_WRITE))):
    target = get_or_404(db, StoryCluster, cluster_id, "Cluster")
    others = [get_or_404(db, StoryCluster, cid, "Cluster") for cid in body.cluster_ids if cid != target.id]
    if not others:
        raise AppError(400, "bad_request", "Choose at least one other cluster to merge")
    merge_clusters(db, target, others)
    record(db, "cluster.merged", f"Merged {len(others)} clusters into “{target.title[:100]}”", actor=current,
           entity_type="cluster", entity_id=target.id)
    db.commit()
    return _out(db, [target])[0]


@router.post("/{cluster_id}/split")
def split(cluster_id: uuid.UUID, body: ClusterSplitIn, db: Session = Depends(get_db),
          current: CurrentUser = Depends(require(P.CLUSTERS_WRITE))):
    c = get_or_404(db, StoryCluster, cluster_id, "Cluster")
    if len(body.item_ids) >= c.item_count:
        raise AppError(400, "bad_request", "Leave at least one report in the original cluster")
    try:
        new = split_cluster(db, c, body.item_ids, body.title)
    except ValueError as exc:
        raise AppError(400, "bad_request", str(exc)) from exc
    record(db, "cluster.split", f"Split {len(body.item_ids)} reports out of “{c.title[:100]}”", actor=current,
           entity_type="cluster", entity_id=c.id, details={"new_cluster": str(new.id)})
    db.commit()
    return {"original": _out(db, [c])[0], "new": _out(db, [new])[0]}


@router.post("/{cluster_id}/items")
def add_to_cluster(cluster_id: uuid.UUID, body: ClusterItemIn, db: Session = Depends(get_db),
                   current: CurrentUser = Depends(require(P.CLUSTERS_WRITE))):
    c = get_or_404(db, StoryCluster, cluster_id, "Cluster")
    item = get_or_404(db, NewsItem, body.news_item_id, "News item")
    add_item(db, c, item.id)
    record(db, "cluster.item_added", f"Added “{item.title[:80]}” to cluster", actor=current, entity_type="cluster", entity_id=c.id)
    db.commit()
    return _out(db, [c])[0]


@router.delete("/{cluster_id}/items/{item_id}")
def remove_from_cluster(cluster_id: uuid.UUID, item_id: uuid.UUID, db: Session = Depends(get_db),
                        current: CurrentUser = Depends(require(P.CLUSTERS_WRITE))):
    c = get_or_404(db, StoryCluster, cluster_id, "Cluster")
    remove_item(db, c, item_id)
    record(db, "cluster.item_removed", f"Removed a report from “{c.title[:100]}”", actor=current,
           entity_type="cluster", entity_id=c.id, details={"news_item_id": str(item_id)})
    db.commit()
    return _out(db, [c])[0]
