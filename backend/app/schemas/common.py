from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class IdName(ORM):
    id: uuid.UUID
    name: str


class Message(BaseModel):
    ok: bool = True
    message: str | None = None


class CategoryOut(ORM):
    id: uuid.UUID
    name: str
    slug: str
    description: str | None = None


class CategoryIn(BaseModel):
    name: str
    description: str | None = None


class GeoEntityOut(ORM):
    id: uuid.UUID
    name: str
    kind: str
    code: str | None = None
    country_code: str | None = None
    parent_id: uuid.UUID | None = None
    is_custom: bool = False
    population: int | None = None
    description: str | None = None
    member_ids: list | None = None


class AuditOut(ORM):
    id: uuid.UUID
    actor_label: str
    action: str
    entity_type: str | None
    entity_id: str | None
    summary: str
    details: dict | None
    ip: str | None
    created_at: datetime
