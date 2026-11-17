"""Duplicate detection, levels 1-4.

Level 1  exact normalised URL          -> same record (enforced by a UNIQUE index)
Level 2  canonical URL                 -> same record
Level 3  content hash (title+summary)  -> new record flagged duplicate_of (syndicated copy)
Level 4  near-identical headline       -> new record flagged duplicate_of
Level 5 (shared keywords + metadata) is "related coverage" and handled by clustering.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from rapidfuzz import fuzz, process
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.text import fold, tokens
from app.models import NewsItem


def find_same_article(db: Session, normalized_url: str, canonical: str | None) -> NewsItem | None:
    item = db.scalar(select(NewsItem).where(NewsItem.normalized_url == normalized_url))
    if item is not None or not canonical:
        return item
    return db.scalar(
        select(NewsItem)
        .where(or_(NewsItem.normalized_url == canonical, NewsItem.canonical_url == canonical))
        .limit(1)
    )


def find_content_duplicate(db: Session, content_hash: str, since: datetime) -> NewsItem | None:
    return db.scalar(
        select(NewsItem)
        .where(NewsItem.content_hash == content_hash, NewsItem.retrieved_at >= since)
        .order_by(NewsItem.retrieved_at.asc())
        .limit(1)
    )


@dataclass
class HeadlineIndex:
    """Recent headlines for level-4 comparison, loaded once per collection batch."""

    ids: list[uuid.UUID] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)
    originals: dict[uuid.UUID, uuid.UUID] = field(default_factory=dict)  # duplicate -> original

    @classmethod
    def load(cls, db: Session, since: datetime, limit: int = 20000) -> HeadlineIndex:
        idx = cls()
        rows = db.execute(
            select(NewsItem.id, NewsItem.title, NewsItem.duplicate_of_id)
            .where(NewsItem.retrieved_at >= since)
            .order_by(NewsItem.retrieved_at.desc())
            .limit(limit)
        ).all()
        for rid, title, dup_of in reversed(rows):
            idx.add(rid, title, dup_of)
        return idx

    def add(self, item_id: uuid.UUID, title: str, duplicate_of: uuid.UUID | None = None) -> None:
        self.ids.append(item_id)
        self.titles.append(fold(title))
        if duplicate_of:
            self.originals[item_id] = duplicate_of

    def best(self, title: str, threshold: float, min_tokens: int) -> tuple[uuid.UUID, float] | None:
        folded = fold(title)
        if len(tokens(folded, folded=True)) < min_tokens or not self.titles:
            return None
        hit = process.extractOne(folded, self.titles, scorer=fuzz.token_sort_ratio, score_cutoff=threshold)
        if hit is None:
            return None
        _, score, pos = hit
        matched = self.ids[pos]
        return self.originals.get(matched, matched), float(score)


def window_start(now: datetime, hours: float) -> datetime:
    return now - timedelta(hours=hours)
