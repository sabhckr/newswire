from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, ForeignKey, Index, Integer, String, Table, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.core.db import Base, JSONType, Timestamps, UTCDateTime, UUIDPk, utcnow


class Category(UUIDPk, Timestamps, Base):
    __tablename__ = "categories"
    name: Mapped[str] = mapped_column(String(80), unique=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str | None] = mapped_column(String(255))


source_categories = Table(
    "source_categories",
    Base.metadata,
    Column("source_id", Uuid, ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True),
    Column("category_id", Uuid, ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
)


class Source(UUIDPk, Timestamps, Base):
    __tablename__ = "sources"
    __table_args__ = (
        Index(
            "uq_sources_feed_url_live",
            "feed_url",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    name: Mapped[str] = mapped_column(String(200))
    publisher: Mapped[str] = mapped_column(String(200))
    # rss | atom | json (JSON Feed or a permitted JSON API with a field mapping)
    source_type: Mapped[str] = mapped_column(String(16), default="rss")
    feed_url: Mapped[str] = mapped_column(String(2048))
    website_url: Mapped[str | None] = mapped_column(String(2048))
    country_code: Mapped[str | None] = mapped_column(String(2), index=True)
    region: Mapped[str | None] = mapped_column(String(120))
    city: Mapped[str | None] = mapped_column(String(120))
    language: Mapped[str | None] = mapped_column(String(16))
    polling_interval_seconds: Mapped[int] = mapped_column(Integer, default=300)
    priority: Mapped[int] = mapped_column(Integer, default=3)  # 1 = highest, 5 = lowest
    reliability_notes: Mapped[str | None] = mapped_column(Text)
    terms_notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    respect_robots: Mapped[bool] = mapped_column(Boolean, default=True)
    extract_page_metadata: Mapped[bool] = mapped_column(Boolean, default=False)
    api_config: Mapped[dict | None] = mapped_column(JSONType)

    last_fetched_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_http_status: Mapped[int | None] = mapped_column(Integer)
    etag: Mapped[str | None] = mapped_column(String(512))
    last_modified: Mapped[str | None] = mapped_column(String(128))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    next_fetch_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    total_items: Mapped[int] = mapped_column(Integer, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    categories: Mapped[list[Category]] = relationship(secondary=source_categories, lazy="selectin")

    @property
    def health(self) -> str:
        if not self.is_active:
            return "disabled"
        if self.last_fetched_at is None:
            return "pending"
        if self.consecutive_failures >= 3:
            return "failing"
        if self.consecutive_failures > 0:
            return "degraded"
        return "healthy"


class SourceFetchLog(UUIDPk, Base):
    __tablename__ = "source_fetch_logs"
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    trigger: Mapped[str] = mapped_column(String(16))  # scheduled | manual | test
    # success | not_modified | error | blocked
    status: Mapped[str] = mapped_column(String(16), index=True)
    http_status: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    items_found: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    items_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    items_skipped: Mapped[int] = mapped_column(Integer, default=0)
    bytes_received: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
