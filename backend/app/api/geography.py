from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.api.common import get_or_404
from app.core.db import get_db
from app.core.errors import AppError
from app.geography.gazetteer import invalidate_gazetteer
from app.models import GeographicEntity
from app.schemas.common import GeoEntityOut
from app.security import permissions as P
from app.security.deps import CurrentUser, require
from app.services.audit import record

router = APIRouter(prefix="/geography", tags=["geography"])


class CustomAreaIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    description: str | None = None
    member_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


@router.get("/entities", response_model=list[GeoEntityOut])
def search_entities(
    q: str | None = Query(None, max_length=100),
    kind: list[Literal["continent", "country", "admin1", "admin2", "city", "custom"]] | None = Query(None),
    parent_id: uuid.UUID | None = None,
    country_code: str | None = None,
    limit: int = Query(25, ge=1, le=300),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require(P.READ)),
):
    stmt = select(GeographicEntity)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(GeographicEntity.name.ilike(like), GeographicEntity.code == q.strip().upper()))
    if kind:
        stmt = stmt.where(GeographicEntity.kind.in_(kind))
    if parent_id:
        stmt = stmt.where(GeographicEntity.parent_id == parent_id)
    if country_code:
        stmt = stmt.where(GeographicEntity.country_code == country_code.upper())
    rank = case(
        (GeographicEntity.kind == "continent", 0), (GeographicEntity.kind == "country", 1),
        (GeographicEntity.kind == "custom", 2), (GeographicEntity.kind == "admin1", 3), else_=4,
    )
    stmt = stmt.order_by(rank, func.coalesce(GeographicEntity.population, 0).desc(), GeographicEntity.name).limit(limit)
    return list(db.scalars(stmt))


@router.get("/entities/{entity_id}")
def get_entity(entity_id: uuid.UUID, db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.READ))):
    ent = get_or_404(db, GeographicEntity, entity_id, "Place")
    chain = []
    cur = ent.parent
    while cur is not None and len(chain) < 6:
        chain.append({"id": str(cur.id), "name": cur.name, "kind": cur.kind})
        cur = cur.parent
    return {**GeoEntityOut.model_validate(ent).model_dump(mode="json"), "ancestors": chain, "alt_names": ent.alt_names}


@router.post("/areas", response_model=GeoEntityOut, status_code=201)
def create_area(body: CustomAreaIn, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.ASSIGNMENTS_WRITE))):
    members = list(db.scalars(select(GeographicEntity).where(GeographicEntity.id.in_(body.member_ids))))
    if len(members) != len(set(body.member_ids)):
        raise AppError(404, "not_found", "One or more member places do not exist")
    ent = GeographicEntity(name=body.name, kind="custom", is_custom=True, description=body.description,
                           member_ids=[str(m.id) for m in members], data_source="user")
    db.add(ent)
    db.flush()
    record(db, "geography.area_created", f"Custom area {ent.name} created ({len(members)} places)", actor=current,
           entity_type="geography", entity_id=ent.id)
    db.commit()
    invalidate_gazetteer()
    return ent


@router.delete("/areas/{entity_id}")
def delete_area(entity_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.ASSIGNMENTS_WRITE))):
    ent = get_or_404(db, GeographicEntity, entity_id, "Place")
    if not ent.is_custom:
        raise AppError(409, "conflict", "Only user-defined areas can be deleted")
    record(db, "geography.area_deleted", f"Custom area {ent.name} deleted", actor=current, entity_type="geography", entity_id=ent.id)
    db.delete(ent)
    db.commit()
    invalidate_gazetteer()
    return {"ok": True}
