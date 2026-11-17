from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError, not_found
from app.geography.gazetteer import invalidate_gazetteer
from app.security import permissions as P
from app.security.deps import CurrentUser, require
from app.services.audit import record
from app.services.settings import DEFAULTS, all_settings, get_setting, set_setting

router = APIRouter(prefix="/settings", tags=["settings"])

# Basic guard rails; values outside these ranges are rejected rather than silently clamped.
RANGES: dict[str, tuple[float, float]] = {
    "fuzzy_threshold": (60, 100), "headline_threshold": (50, 100), "keyword_jaccard": (0.05, 1.0),
    "geo_min_confidence": (0.0, 1.0), "retention_count": (1, 365), "interval_hours": (1, 720),
    "window_hours": (1, 720), "backfill_days": (0, 90), "fetch_log_retention_days": (1, 3650),
}


@router.get("")
def get_all(db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.READ))):
    return {"values": all_settings(db), "defaults": DEFAULTS}


@router.put("/{key}")
def update(key: str, value: dict[str, Any] = Body(...), db: Session = Depends(get_db),
           current: CurrentUser = Depends(require(P.SETTINGS_WRITE))):
    if key not in DEFAULTS:
        raise not_found("Setting")
    for k, v in value.items():
        if k in RANGES and isinstance(v, (int, float)) and not isinstance(v, bool):
            lo, hi = RANGES[k]
            if not lo <= v <= hi:
                raise AppError(422, "validation_error", f"{k} must be between {lo} and {hi}")
    if key == "matching.options" and value.get("excluded_mode") not in (None, "reject", "penalty"):
        raise AppError(422, "validation_error", "excluded_mode must be reject or penalty")
    before = get_setting(db, key)
    try:
        after = set_setting(db, key, value, user_id=current.id)
    except (TypeError, ValueError) as exc:
        raise AppError(422, "validation_error", str(exc)) from exc
    diff = {k: {"from": before.get(k), "to": v} for k, v in after.items() if before.get(k) != v}
    if diff:
        record(db, "settings.updated", f"Settings {key} changed: {', '.join(diff)}", actor=current,
               entity_type="setting", entity_id=key, details=diff)
    db.commit()
    if key.startswith("matching"):
        invalidate_gazetteer()
    return {"key": key, "value": after}
