"""What happens to new reports after they are stored: geography -> clustering -> matching.

Each stage runs in its own savepoint so a failure in one stage never loses the ingested
reports or blocks the other stages.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.clustering.engine import cluster_items
from app.geography.classify import classify_and_store
from app.geography.gazetteer import get_gazetteer
from app.matching.service import match_items
from app.models import NewsItem

log = logging.getLogger(__name__)


def post_process_items(db: Session, items: list[NewsItem]) -> dict:
    stats: dict = {"items": len(items)}
    if not items:
        return stats
    try:
        with db.begin_nested():
            gaz = get_gazetteer(db)
            for item in items:
                classify_and_store(db, item, item.source, gaz)
            db.flush()
        stats["geography"] = "ok"
    except Exception:
        log.exception("Geographic classification failed")
        stats["geography"] = "failed"
    try:
        with db.begin_nested():
            stats["clustering"] = cluster_items(db, items)
    except Exception:
        log.exception("Clustering failed")
        stats["clustering"] = "failed"
    try:
        with db.begin_nested():
            stats["matches"] = match_items(db, items)
    except Exception:
        log.exception("Matching failed")
        stats["matches"] = "failed"
    return stats
