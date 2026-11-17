from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.core.db import Base, JSONType, Timestamps, UTCDateTime, UUIDPk, utcnow


class GeographicEntity(UUIDPk, Timestamps, Base):
    """A place: continent, country, admin region, city, or a user-defined area."""

    __tablename__ = "geographic_entities"

    name: Mapped[str] = mapped_column(String(200), index=True)
    # continent | country | admin1 | admin2 | city | custom
    kind: Mapped[str] = mapped_column(String(16), index=True)
    code: Mapped[str | None] = mapped_column(String(20), index=True)  # continent code / ISO2 / admin code
    country_code: Mapped[str | None] = mapped_column(String(2), index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("geographic_entities.id", ondelete="SET NULL"), index=True
    )
    geonames_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    alt_names: Mapped[list | None] = mapped_column(JSONType)
    population: Mapped[int | None] = mapped_column(BigInteger)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False)
    # Ambiguous names (e.g. "Chad", "Jordan") only count when capitalised in the text.
    is_ambiguous: Mapped[bool] = mapped_column(Boolean, default=False)
    # For custom areas: member entities whose mentions count as this area.
    member_ids: Mapped[list | None] = mapped_column(JSONType)
    description: Mapped[str | None] = mapped_column(Text)
    data_source: Mapped[str | None] = mapped_column(String(64))

    parent: Mapped[GeographicEntity | None] = relationship(remote_side="GeographicEntity.id", lazy="joined")


class NewsGeography(UUIDPk, Base):
    """A place associated with a news item, with its role, provenance and confidence.

    role: publisher (where the outlet is based), event (dateline / editor-set),
          article (feed metadata), mentioned (place named in headline/summary).
    """

    __tablename__ = "news_geographies"

    news_item_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("news_items.id", ondelete="CASCADE"), index=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("geographic_entities.id", ondelete="SET NULL"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    original_text: Mapped[str | None] = mapped_column(String(300))
    country_code: Mapped[str | None] = mapped_column(String(2), index=True)
    region: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(200))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    # source_metadata | feed_metadata | feed_category | dateline | text_match | editor
    method: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    entity: Mapped[GeographicEntity | None] = relationship(lazy="joined")
