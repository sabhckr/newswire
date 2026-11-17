from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.core.db import Base, JSONType, Timestamps, UTCDateTime, UUIDPk, utcnow
from app.models.geography import GeographicEntity
from app.models.news import NewsItem
from app.models.sources import Category, Source

assignment_sources = Table(
    "assignment_sources",
    Base.metadata,
    Column("assignment_id", Uuid, ForeignKey("editorial_assignments.id", ondelete="CASCADE"), primary_key=True),
    Column("source_id", Uuid, ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True),
)

assignment_geographies = Table(
    "assignment_geographies",
    Base.metadata,
    Column("assignment_id", Uuid, ForeignKey("editorial_assignments.id", ondelete="CASCADE"), primary_key=True),
    Column("entity_id", Uuid, ForeignKey("geographic_entities.id", ondelete="CASCADE"), primary_key=True),
)


class EditorialAssignment(UUIDPk, Timestamps, Base):
    """An editor's research brief. `headline` is the editor's own wording, never a source headline."""

    __tablename__ = "editorial_assignments"

    headline: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    category_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("categories.id", ondelete="SET NULL"))
    language: Mapped[str | None] = mapped_column(String(16))
    priority: Mapped[str] = mapped_column(String(16), default="normal")  # low|normal|high|urgent
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active|paused|archived
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    monitoring_interval_seconds: Mapped[int | None] = mapped_column(Integer)
    min_score: Mapped[float] = mapped_column(Float, default=25.0)
    restrict_to_sources: Mapped[bool] = mapped_column(Boolean, default=False)
    geo_mode: Mapped[str] = mapped_column(String(16), default="boost")  # boost | require
    city: Mapped[str | None] = mapped_column(String(200))
    research_status: Mapped[str] = mapped_column(String(16), default="open")  # open|in_progress|done
    editorial_notes: Mapped[str | None] = mapped_column(Text)
    source_checklist: Mapped[list | None] = mapped_column(JSONType)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    last_matched_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    match_count: Mapped[int] = mapped_column(Integer, default=0)

    category: Mapped[Category | None] = relationship(lazy="joined")
    keywords: Mapped[list[AssignmentKeyword]] = relationship(
        back_populates="assignment", cascade="all, delete-orphan", lazy="selectin",
        order_by="AssignmentKeyword.position",
    )
    sources: Mapped[list[Source]] = relationship(secondary=assignment_sources, lazy="selectin")
    geographies: Mapped[list[GeographicEntity]] = relationship(secondary=assignment_geographies, lazy="selectin")


class AssignmentKeyword(UUIDPk, Base):
    __tablename__ = "assignment_keywords"
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("editorial_assignments.id", ondelete="CASCADE"), index=True
    )
    term: Mapped[str] = mapped_column(String(200))
    # primary | secondary | synonym | required | excluded
    kind: Mapped[str] = mapped_column(String(16))
    weight: Mapped[float | None] = mapped_column(Float)  # overrides the global weight when set
    position: Mapped[int] = mapped_column(Integer, default=0)

    assignment: Mapped[EditorialAssignment] = relationship(back_populates="keywords")


class AssignmentMatch(UUIDPk, Timestamps, Base):
    __tablename__ = "assignment_matches"
    __table_args__ = (
        UniqueConstraint("assignment_id", "news_item_id", name="uq_assignment_matches_pair"),
        Index("ix_assignment_matches_assignment_score", "assignment_id", "score"),
    )

    assignment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("editorial_assignments.id", ondelete="CASCADE"), index=True
    )
    news_item_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("news_items.id", ondelete="CASCADE"), index=True)
    score: Mapped[float] = mapped_column(Float)
    breakdown: Mapped[list | None] = mapped_column(JSONType)
    explanation: Mapped[str] = mapped_column(Text)
    matched_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    # unreviewed | verified | rejected | archived
    review_status: Mapped[str] = mapped_column(String(16), default="unreviewed", index=True)
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    notes: Mapped[str | None] = mapped_column(Text)

    news_item: Mapped[NewsItem] = relationship(lazy="joined")
    assignment: Mapped[EditorialAssignment] = relationship(lazy="noload")
