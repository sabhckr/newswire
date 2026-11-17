from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.core.db import Base, JSONType, UTCDateTime, UUIDPk, utcnow


class AuditLog(UUIDPk, Base):
    __tablename__ = "audit_logs"

    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"), index=True)
    actor_label: Mapped[str] = mapped_column(String(320), default="system")
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(32), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), index=True)
    summary: Mapped[str] = mapped_column(String(500))
    details: Mapped[dict | None] = mapped_column(JSONType)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict | list | int | float | str | bool | None] = mapped_column(JSONType)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))


class ScheduledJob(Base):
    """Heartbeat and last-run record for each recurring job of the worker."""

    __tablename__ = "scheduled_jobs"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str | None] = mapped_column(String(255))
    interval_seconds: Mapped[int | None] = mapped_column(Integer)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_status: Mapped[str | None] = mapped_column(String(16))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_duration_ms: Mapped[int | None] = mapped_column(Integer)
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    worker_id: Mapped[str | None] = mapped_column(String(128))
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Backup(UUIDPk, Base):
    __tablename__ = "backups"

    filename: Mapped[str] = mapped_column(String(255), unique=True)
    kind: Mapped[str] = mapped_column(String(16))  # manual | scheduled
    status: Mapped[str] = mapped_column(String(16))  # running | completed | failed | deleted
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    includes: Mapped[list | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    verify_status: Mapped[str | None] = mapped_column(String(300))
    error: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
