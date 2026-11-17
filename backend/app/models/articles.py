from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.core.db import Base, JSONType, Timestamps, UTCDateTime, UUIDPk, utcnow
from app.models.images import ImageAsset
from app.models.sources import Category


class Tag(UUIDPk, Base):
    __tablename__ = "tags"
    name: Mapped[str] = mapped_column(String(80), unique=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)


article_tags = Table(
    "article_tags",
    Base.metadata,
    Column("article_id", Uuid, ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Uuid, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)

article_assignments = Table(
    "article_assignments",
    Base.metadata,
    Column("article_id", Uuid, ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True),
    Column("assignment_id", Uuid, ForeignKey("editorial_assignments.id", ondelete="CASCADE"), primary_key=True),
)


class Article(UUIDPk, Timestamps, Base):
    """Original NEWSWIRE journalism written by an editor."""

    __tablename__ = "articles"

    headline: Mapped[str] = mapped_column(String(500))
    subtitle: Mapped[str | None] = mapped_column(String(500))
    slug: Mapped[str] = mapped_column(String(255), unique=True)
    body_json: Mapped[dict | None] = mapped_column(JSONType)
    body_html: Mapped[str] = mapped_column(Text, default="")
    excerpt: Mapped[str | None] = mapped_column(Text)
    seo_title: Mapped[str | None] = mapped_column(String(255))
    seo_description: Mapped[str | None] = mapped_column(String(500))
    category_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("categories.id", ondelete="SET NULL"))
    featured_image_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("image_assets.id", ondelete="SET NULL")
    )
    featured_image_caption: Mapped[str | None] = mapped_column(Text)
    geo_entity_ids: Mapped[list | None] = mapped_column(JSONType)
    # draft | in_review | approved | scheduled | published | unpublished | archived
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    author_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    last_published_version: Mapped[int | None] = mapped_column(Integer)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    category: Mapped[Category | None] = relationship(lazy="joined")
    featured_image: Mapped[ImageAsset | None] = relationship(lazy="joined")
    tags: Mapped[list[Tag]] = relationship(secondary=article_tags, lazy="selectin")
    sources: Mapped[list[ArticleSource]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="ArticleSource.position"
    )
    images: Mapped[list[ArticleImage]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="ArticleImage.position"
    )


class ArticleSource(UUIDPk, Base):
    """A reference to source reporting consulted for an article."""

    __tablename__ = "article_sources"
    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    news_item_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("news_items.id", ondelete="SET NULL"))
    url: Mapped[str] = mapped_column(String(2048))
    title: Mapped[str | None] = mapped_column(Text)
    publisher: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ArticleImage(UUIDPk, Base):
    __tablename__ = "article_images"
    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    image_asset_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("image_assets.id", ondelete="CASCADE"))
    caption: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, default=0)
    # True when the editor intends to crop/alter the image (blocked for no-derivatives licences).
    is_modified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    image: Mapped[ImageAsset] = relationship(lazy="joined")
