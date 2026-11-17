from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.core.db import Base, Timestamps, UTCDateTime, UUIDPk, utcnow
from app.models.news import NewsItem


class StoryCluster(UUIDPk, Timestamps, Base):
    __tablename__ = "story_clusters"

    title: Mapped[str] = mapped_column(String(500))
    # source_headline (taken from the earliest report) | editor (manually set)
    title_source: Mapped[str] = mapped_column(String(16), default="source_headline")
    representative_item_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("news_items.id", ondelete="SET NULL")
    )
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    publisher_count: Mapped[int] = mapped_column(Integer, default=0)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    notes: Mapped[str | None] = mapped_column(Text)

    items: Mapped[list[StoryClusterItem]] = relationship(
        back_populates="cluster", cascade="all, delete-orphan", lazy="selectin"
    )


class StoryClusterItem(Base):
    __tablename__ = "story_cluster_items"

    cluster_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("story_clusters.id", ondelete="CASCADE"), primary_key=True
    )
    news_item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("news_items.id", ondelete="CASCADE"), primary_key=True, unique=True
    )
    similarity: Mapped[float | None] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(32))  # headline | keywords | duplicate | editor | seed
    added_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    cluster: Mapped[StoryCluster] = relationship(back_populates="items")
    news_item: Mapped[NewsItem] = relationship(lazy="joined")
