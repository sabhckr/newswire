from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.core.db import Base, Timestamps, UUIDPk


class VideoAsset(UUIDPk, Timestamps, Base):
    """An uploaded video file (MP4 or MOV) for an article, a Facebook Reel or YouTube."""

    __tablename__ = "video_assets"

    title: Mapped[str | None] = mapped_column(String(500))
    original_filename: Mapped[str | None] = mapped_column(String(255))
    # Relative to the media root, e.g. videos/2026/11/<uuid>.mp4
    local_path: Mapped[str] = mapped_column(String(500))
    mime: Mapped[str] = mapped_column(String(64))
    file_size: Mapped[int] = mapped_column(BigInteger)
    file_sha256: Mapped[str] = mapped_column(String(64), index=True)
    # Display size (rotation applied); None when the file does not say.
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
