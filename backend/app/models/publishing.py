from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.core.db import Base, JSONType, Timestamps, UTCDateTime, UUIDPk


class PublishingDestination(UUIDPk, Timestamps, Base):
    __tablename__ = "publishing_destinations"

    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(16))  # builtin | wordpress | webhook | facebook | youtube
    base_url: Mapped[str | None] = mapped_column(String(2048))
    username: Mapped[str | None] = mapped_column(String(200))
    secret_encrypted: Mapped[str | None] = mapped_column(Text)  # never returned by the API
    config: Mapped[dict | None] = mapped_column(JSONType)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # A commercial publication may not use NonCommercial-licensed images.
    is_commercial: Mapped[bool] = mapped_column(Boolean, default=True)
    # Only honoured when the admin also enables the global auto-publish policy.
    auto_publish: Mapped[bool] = mapped_column(Boolean, default=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_check_status: Mapped[str | None] = mapped_column(String(300))


class PublishingJob(UUIDPk, Timestamps, Base):
    __tablename__ = "publishing_jobs"

    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    destination_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("publishing_destinations.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(String(16))  # publish | update | unpublish | draft
    # pending | running | succeeded | failed | cancelled
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    article_version: Mapped[int] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    scheduled_for: Mapped[datetime | None] = mapped_column(UTCDateTime)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    remote_id: Mapped[str | None] = mapped_column(String(200))
    remote_url: Mapped[str | None] = mapped_column(String(2048))
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Per-destination choices: Facebook format (post | reel), YouTube privacy, social caption,
    # and the website jobs of the same request that a social post waits for (its link).
    options: Mapped[dict | None] = mapped_column(JSONType)

    destination: Mapped[PublishingDestination] = relationship(lazy="joined")


class ArticlePublication(UUIDPk, Timestamps, Base):
    """Remote state of an article at one destination."""

    __tablename__ = "article_publications"
    __table_args__ = (UniqueConstraint("article_id", "destination_id", name="uq_article_publications_pair"),)

    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    destination_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("publishing_destinations.id", ondelete="CASCADE")
    )
    remote_id: Mapped[str | None] = mapped_column(String(200))
    remote_url: Mapped[str | None] = mapped_column(String(2048))
    remote_status: Mapped[str] = mapped_column(String(32))  # published | draft | unpublished | unknown
    published_version: Mapped[int | None] = mapped_column(Integer)
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    sync_error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSONType)

    destination: Mapped[PublishingDestination] = relationship(lazy="joined")
