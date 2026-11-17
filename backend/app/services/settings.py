"""Runtime-configurable settings stored in `system_settings`, merged over defaults."""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy.orm import Session

from app.models import SystemSetting

DEFAULTS: dict[str, dict[str, Any]] = {
    "matching.weights": {
        "required_phrase": 30.0,
        "primary": 15.0,
        "synonym": 12.0,
        "secondary": 7.0,
        "geography": 20.0,
        "category": 10.0,
        "selected_publisher": 10.0,
        "recency_max": 15.0,
        "language": 5.0,
        "excluded_penalty": -50.0,
    },
    "matching.options": {
        "excluded_mode": "reject",  # reject | penalty
        "fuzzy_enabled": True,
        "fuzzy_threshold": 88,
        "fuzzy_min_length": 5,
        "fuzzy_factor": 0.5,
        "inflection_factor": 0.9,
        "token_factor": 0.6,
        "body_factor": 0.75,
        "recency_window_hours": 72,
        "geo_min_confidence": 0.5,
        "geo_include_publisher": False,
        "backfill_days": 7,
        "backfill_limit": 5000,
    },
    "dedup": {
        "headline_threshold": 95,
        "headline_window_hours": 72,
        "min_headline_tokens": 5,
        "content_hash_window_days": 30,
    },
    "clustering": {
        "enabled": True,
        "window_hours": 48,
        "headline_threshold": 85,
        "min_shared_title_tokens": 3,
        "keyword_jaccard": 0.30,
        "min_shared_tokens": 4,
        "related_headline_floor": 50,
    },
    "collection": {
        "enabled": True,
        "fetch_log_retention_days": 30,
    },
    "publishing": {
        # Off by default: nothing is published without an explicit, approved request.
        "auto_publish_policy": False,
        "block_on_template_markers": True,
    },
    "backups": {
        "enabled": True,
        "interval_hours": 24,
        "retention_count": 7,
        "include_images": True,
    },
}


def get_setting(db: Session, key: str) -> dict[str, Any]:
    base = copy.deepcopy(DEFAULTS.get(key, {}))
    row = db.get(SystemSetting, key)
    if row is not None and isinstance(row.value, dict):
        for k, v in row.value.items():
            if k in base or not base:
                base[k] = v
    return base


def set_setting(db: Session, key: str, value: dict[str, Any], user_id=None) -> dict[str, Any]:
    if key not in DEFAULTS:
        raise KeyError(key)
    defaults = DEFAULTS[key]
    clean: dict[str, Any] = {}
    for k, v in value.items():
        if k not in defaults:
            continue
        default = defaults[k]
        if isinstance(default, bool):
            clean[k] = bool(v)
        elif isinstance(default, (int, float)) and not isinstance(default, bool):
            clean[k] = type(default)(v)
        else:
            clean[k] = v
    row = db.get(SystemSetting, key)
    if row is None:
        row = SystemSetting(key=key, value=clean, updated_by_id=user_id)
        db.add(row)
    else:
        merged = dict(row.value or {})
        merged.update(clean)
        row.value = merged
        row.updated_by_id = user_id
    db.flush()
    return get_setting(db, key)


def all_settings(db: Session) -> dict[str, dict[str, Any]]:
    return {k: get_setting(db, k) for k in DEFAULTS}
