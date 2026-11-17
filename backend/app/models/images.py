from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.core.db import Base, JSONType, Timestamps, UTCDateTime, UUIDPk, utcnow


class ImageLicense(UUIDPk, Base):
    __tablename__ = "image_licenses"
    __table_args__ = (UniqueConstraint("code", "version", name="uq_image_licenses_code_version"),)

    # cc0 | pdm | by | by-sa | by-nc | by-nd | by-nc-sa | by-nc-nd | own | other | unknown
    code: Mapped[str] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(10), default="")
    name: Mapped[str] = mapped_column(String(120))
    url: Mapped[str | None] = mapped_column(String(500))
    # None = not documented; never assumed.
    allows_commercial: Mapped[bool | None] = mapped_column(Boolean)
    allows_derivatives: Mapped[bool | None] = mapped_column(Boolean)
    requires_attribution: Mapped[bool | None] = mapped_column(Boolean)
    share_alike: Mapped[bool] = mapped_column(Boolean, default=False)
    is_known: Mapped[bool] = mapped_column(Boolean, default=True)


class ImageAsset(UUIDPk, Timestamps, Base):
    __tablename__ = "image_assets"
    __table_args__ = (UniqueConstraint("provider", "provider_id", name="uq_image_assets_provider_item"),)

    provider: Mapped[str] = mapped_column(String(32))  # openverse | wikimedia | upload
    provider_id: Mapped[str] = mapped_column(String(300))
    repository: Mapped[str | None] = mapped_column(String(120))  # e.g. flickr, wikimedia
    title: Mapped[str | None] = mapped_column(String(500))
    creator: Mapped[str | None] = mapped_column(String(500))
    creator_url: Mapped[str | None] = mapped_column(String(2048))
    source_page_url: Mapped[str | None] = mapped_column(String(2048))
    original_url: Mapped[str | None] = mapped_column(String(2048))
    thumbnail_url: Mapped[str | None] = mapped_column(String(2048))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    mime: Mapped[str | None] = mapped_column(String(64))
    license_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("image_licenses.id"))
    license_raw: Mapped[str | None] = mapped_column(String(300))
    license_url: Mapped[str | None] = mapped_column(String(500))
    attribution_text: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # not_downloaded | downloaded | failed
    download_status: Mapped[str] = mapped_column(String(16), default="not_downloaded")
    download_error: Mapped[str | None] = mapped_column(Text)
    local_path: Mapped[str | None] = mapped_column(String(500))
    thumbnail_path: Mapped[str | None] = mapped_column(String(500))
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    file_size: Mapped[int | None] = mapped_column(Integer)
    # pending | approved | rejected
    approval_status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    notes: Mapped[str | None] = mapped_column(Text)
    assignment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("editorial_assignments.id", ondelete="SET NULL"), index=True
    )
    search_query: Mapped[str | None] = mapped_column(String(300))
    provider_metadata: Mapped[dict | None] = mapped_column(JSONType)

    license: Mapped[ImageLicense | None] = relationship(lazy="joined")
