from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.core.db import Base, JSONType, Timestamps, UTCDateTime, UUIDPk, utcnow
from app.models.sources import Source


class NewsItem(UUIDPk, Timestamps, Base):
    """One report as published by a source. The title is the publisher's original headline."""

    __tablename__ = "news_items"
    __table_args__ = (Index("ix_news_items_source_sort", "source_id", "sort_at"),)

    source_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("sources.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(String(2048))
    normalized_url: Mapped[str] = mapped_column(String(2048), unique=True)
    canonical_url: Mapped[str | None] = mapped_column(String(2048), index=True)
    guid: Mapped[str | None] = mapped_column(String(1024))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    # Only what the feed itself supplies; never scraped full text.
    content: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(300))
    language: Mapped[str | None] = mapped_column(String(16), index=True)
    category_text: Mapped[str | None] = mapped_column(String(500))
    location_text: Mapped[str | None] = mapped_column(String(300))
    image_urls: Mapped[list | None] = mapped_column(JSONType)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    # published_at when known and not in the future, else retrieved_at. Indexed for sorting.
    sort_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)

    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("news_items.id", ondelete="SET NULL")
    )
    duplicate_reason: Mapped[str | None] = mapped_column(String(32))
    duplicate_score: Mapped[float | None] = mapped_column(Float)

    # unreviewed | verified | archived
    review_status: Mapped[str] = mapped_column(String(16), default="unreviewed", index=True)
    extra: Mapped[dict | None] = mapped_column(JSONType)

    source: Mapped[Source | None] = relationship(lazy="joined")


class NewsItemSource(Base):
    """Every feed in which a given article URL appeared (syndication, section feeds)."""

    __tablename__ = "news_item_sources"
    news_item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("news_items.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True
    )
    guid: Mapped[str | None] = mapped_column(String(1024))
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
