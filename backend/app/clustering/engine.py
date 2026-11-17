"""Deterministic story clustering.

Two reports are related when one of these holds:
  duplicate  one is a flagged duplicate of the other (same syndicated story)
  headline   headlines are near-identical after stopword removal (RapidFuzz token-set ratio
             >= threshold) AND share at least `min_shared_title_tokens` significant words
             covering >= 60% of the shorter headline
  keywords   headline+summary share >= `min_shared_tokens` significant words with a Jaccard
             overlap >= `keyword_jaccard`, AND either the headlines are loosely similar or both
             reports name the same country
A single shared keyword never links two reports.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from rapidfuzz import fuzz
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.text import STOPWORDS, fold, significant_tokens, tokens
from app.models import NewsGeography, NewsItem, Source, StoryCluster, StoryClusterItem
from app.services.settings import get_setting


@dataclass
class ItemSig:
    id: uuid.UUID
    title: str
    title_sig: str
    title_tokens: set[str]
    all_tokens: set[str]
    source_id: uuid.UUID | None
    sort_at: datetime
    duplicate_of: uuid.UUID | None
    countries: set[str] = field(default_factory=set)
    cluster_id: uuid.UUID | None = None


def make_sig(item_id, title, summary, source_id, sort_at, duplicate_of, countries=None, cluster_id=None) -> ItemSig:
    folded = fold(title)
    sig_words = [t for t in tokens(folded, folded=True) if t not in STOPWORDS]
    return ItemSig(
        id=item_id,
        title=title,
        title_sig=" ".join(sig_words),
        title_tokens=significant_tokens(title),
        all_tokens=significant_tokens(f"{title} {summary or ''}"),
        source_id=source_id,
        sort_at=sort_at,
        duplicate_of=duplicate_of,
        countries=set(countries or ()),
        cluster_id=cluster_id,
    )


def similarity(a: ItemSig, b: ItemSig, cfg: dict) -> tuple[float, str] | None:
    if a.duplicate_of == b.id or b.duplicate_of == a.id or (a.duplicate_of and a.duplicate_of == b.duplicate_of):
        return 100.0, "duplicate"
    shared_title = a.title_tokens & b.title_tokens
    shortest = min(len(a.title_tokens), len(b.title_tokens)) or 1
    head = fuzz.token_set_ratio(a.title_sig, b.title_sig) if a.title_sig and b.title_sig else 0.0
    if (
        head >= cfg["headline_threshold"]
        and len(shared_title) >= cfg["min_shared_title_tokens"]
        and len(shared_title) / shortest >= 0.6
    ):
        return float(head), "headline"
    shared = a.all_tokens & b.all_tokens
    if len(shared) < cfg["min_shared_tokens"]:
        return None
    union = a.all_tokens | b.all_tokens
    jaccard = len(shared) / len(union) if union else 0.0
    if jaccard >= cfg["keyword_jaccard"] and (head >= cfg["related_headline_floor"] or (a.countries & b.countries)):
        return round(jaccard * 100, 1), "keywords"
    return None


METHOD_RANK = {"duplicate": 3, "headline": 2, "keywords": 1}


def _countries(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, set[str]]:
    out: dict[uuid.UUID, set[str]] = defaultdict(set)
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        rows = db.execute(
            select(NewsGeography.news_item_id, NewsGeography.country_code).where(
                NewsGeography.news_item_id.in_(chunk),
                NewsGeography.role.in_(("mentioned", "event", "article", "editor")),
                NewsGeography.confidence >= 0.5,
                NewsGeography.country_code.is_not(None),
            )
        )
        for nid, cc in rows:
            out[nid].add(cc)
    return out


def load_window(db: Session, since: datetime, limit: int = 4000) -> dict[uuid.UUID, ItemSig]:
    rows = db.execute(
        select(
            NewsItem.id, NewsItem.title, NewsItem.summary, NewsItem.source_id, NewsItem.sort_at,
            NewsItem.duplicate_of_id, StoryClusterItem.cluster_id,
        )
        .outerjoin(StoryClusterItem, StoryClusterItem.news_item_id == NewsItem.id)
        .where(NewsItem.retrieved_at >= since)
        .order_by(NewsItem.retrieved_at.desc())
        .limit(limit)
    ).all()
    countries = _countries(db, [r[0] for r in rows])
    return {
        r[0]: make_sig(r[0], r[1], r[2], r[3], r[4], r[5], countries.get(r[0]), r[6])
        for r in rows
    }


def refresh_cluster(db: Session, cluster: StoryCluster) -> None:
    rows = db.execute(
        select(NewsItem.id, NewsItem.title, NewsItem.sort_at, NewsItem.source_id, Source.publisher)
        .join(StoryClusterItem, StoryClusterItem.news_item_id == NewsItem.id)
        .outerjoin(Source, Source.id == NewsItem.source_id)
        .where(StoryClusterItem.cluster_id == cluster.id)
        .order_by(NewsItem.sort_at.asc())
    ).all()
    cluster.item_count = len(rows)
    if not rows:
        cluster.status = "archived"
        return
    cluster.publisher_count = len({(r[4] or str(r[3])) for r in rows})
    cluster.first_seen_at = rows[0][2]
    cluster.last_seen_at = rows[-1][2]
    cluster.representative_item_id = rows[0][0]
    if cluster.title_source != "editor":
        cluster.title = rows[0][1][:500]


def cluster_items(db: Session, new_items: list[NewsItem]) -> dict:
    cfg = get_setting(db, "clustering")
    if not cfg.get("enabled", True) or not new_items:
        return {"joined": 0, "created": 0}
    since = utcnow() - timedelta(hours=float(cfg["window_hours"]))
    window = load_window(db, since)
    new_ids = [i.id for i in new_items]
    missing = [i for i in new_items if i.id not in window]
    if missing:
        countries = _countries(db, [i.id for i in missing])
        for i in missing:
            window[i.id] = make_sig(i.id, i.title, i.summary, i.source_id, i.sort_at, i.duplicate_of_id, countries.get(i.id))

    inverted: dict[str, set[uuid.UUID]] = defaultdict(set)
    for sig in window.values():
        for tok in sig.all_tokens:
            inverted[tok].add(sig.id)

    touched: set[uuid.UUID] = set()
    stats = {"joined": 0, "created": 0}
    clusters: dict[uuid.UUID, StoryCluster] = {}

    def get_cluster(cid: uuid.UUID) -> StoryCluster | None:
        if cid not in clusters:
            c = db.get(StoryCluster, cid)
            if c is None:
                return None
            clusters[cid] = c
        return clusters[cid]

    for nid in new_ids:
        sig = window.get(nid)
        if sig is None or sig.cluster_id is not None:
            continue
        counts: dict[uuid.UUID, int] = defaultdict(int)
        for tok in sig.all_tokens:
            for other_id in inverted.get(tok, ()):
                if other_id != nid:
                    counts[other_id] += 1
        if sig.duplicate_of and sig.duplicate_of in window:
            counts[sig.duplicate_of] += 100
        best: tuple[int, float, ItemSig, str] | None = None
        for other_id, shared in counts.items():
            if shared < 2:
                continue
            other = window[other_id]
            sim = similarity(sig, other, cfg)
            if sim is None:
                continue
            if other.cluster_id:
                c = get_cluster(other.cluster_id)
                if c is None or c.is_locked or c.status != "active":
                    continue
            rank = (METHOD_RANK[sim[1]], sim[0])
            if best is None or rank > (best[0], best[1]):
                best = (rank[0], rank[1], other, sim[1])
        if best is None:
            continue
        _, score, other, method = best
        if other.cluster_id:
            cluster = get_cluster(other.cluster_id)
            assert cluster is not None
            stats["joined"] += 1
        else:
            first = other if other.sort_at <= sig.sort_at else sig
            cluster = StoryCluster(title=first.title[:500], title_source="source_headline", first_seen_at=first.sort_at,
                                   last_seen_at=max(other.sort_at, sig.sort_at))
            db.add(cluster)
            db.flush()
            clusters[cluster.id] = cluster
            db.add(StoryClusterItem(cluster_id=cluster.id, news_item_id=other.id, similarity=None, method="seed"))
            other.cluster_id = cluster.id
            stats["created"] += 1
        db.add(StoryClusterItem(cluster_id=cluster.id, news_item_id=sig.id, similarity=score, method=method))
        sig.cluster_id = cluster.id
        touched.add(cluster.id)
        db.flush()

    for cid in touched:
        refresh_cluster(db, clusters[cid])
    db.flush()
    return stats


# --- manual editorial operations -----------------------------------------------------------

def merge_clusters(db: Session, target: StoryCluster, others: list[StoryCluster]) -> StoryCluster:
    for other in others:
        if other.id == target.id:
            continue
        for link in list(other.items):
            nid = link.news_item_id
            other.items.remove(link)
            db.flush()
            db.add(StoryClusterItem(cluster_id=target.id, news_item_id=nid, similarity=link.similarity, method="editor"))
        db.flush()
        db.delete(other)
    db.flush()
    db.refresh(target)
    refresh_cluster(db, target)
    db.flush()
    return target


def split_cluster(db: Session, cluster: StoryCluster, item_ids: list[uuid.UUID], title: str | None = None) -> StoryCluster:
    moving = [link for link in cluster.items if link.news_item_id in set(item_ids)]
    if not moving:
        raise ValueError("None of the given items belong to this cluster")
    new = StoryCluster(title=title or "Split cluster", title_source="editor" if title else "source_headline")
    db.add(new)
    db.flush()
    for link in moving:
        cluster.items.remove(link)
        db.flush()
        db.add(StoryClusterItem(cluster_id=new.id, news_item_id=link.news_item_id, similarity=None, method="editor"))
    db.flush()
    db.refresh(new)
    db.refresh(cluster)
    refresh_cluster(db, new)
    refresh_cluster(db, cluster)
    db.flush()
    return new


def remove_item(db: Session, cluster: StoryCluster, item_id: uuid.UUID) -> None:
    for link in list(cluster.items):
        if link.news_item_id == item_id:
            cluster.items.remove(link)
    db.flush()
    refresh_cluster(db, cluster)
    db.flush()


def add_item(db: Session, cluster: StoryCluster, item_id: uuid.UUID) -> None:
    existing = db.scalar(select(StoryClusterItem).where(StoryClusterItem.news_item_id == item_id))
    old_cluster = None
    if existing is not None:
        if existing.cluster_id == cluster.id:
            return
        old_cluster = db.get(StoryCluster, existing.cluster_id)
        db.delete(existing)
        db.flush()
    db.add(StoryClusterItem(cluster_id=cluster.id, news_item_id=item_id, method="editor"))
    db.flush()
    db.refresh(cluster)
    refresh_cluster(db, cluster)
    if old_cluster is not None:
        db.refresh(old_cluster)
        refresh_cluster(db, old_cluster)
    db.flush()


def cluster_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(StoryCluster).where(StoryCluster.status == "active")) or 0
