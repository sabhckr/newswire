"""Create sources from a NEWSWIRE sources export, an OPML list or a bundled source pack."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.http import validate_url
from app.models import Category, Source
from app.schemas.domain import SourceIn

PACK_DIR = Path(__file__).resolve().parents[1] / "data" / "source_packs"
MAX_ENTRIES = 2000


def live_duplicate(db: Session, url: str) -> bool:
    return db.scalar(select(Source.id).where(Source.feed_url == url, Source.deleted_at.is_(None))) is not None


def import_entries(db: Session, entries: list[dict]) -> dict:
    """Validate and add sources. Feeds already present (same URL) are skipped. Does not commit."""
    created, skipped, errors = 0, 0, []
    cats = {c.name.lower(): c for c in db.scalars(select(Category))}
    for i, e in enumerate(entries[:MAX_ENTRIES]):
        try:
            body = SourceIn(**{k: v for k, v in e.items() if k in SourceIn.model_fields and k != "category_ids"})
            validate_url(body.feed_url)
        except Exception as exc:  # noqa: BLE001 - every bad row is reported, none stops the import
            errors.append({"index": i, "feed_url": e.get("feed_url"), "error": str(exc)[:200]})
            continue
        if live_duplicate(db, body.feed_url):
            skipped += 1
            continue
        s = Source()
        for k, v in body.model_dump(exclude={"category_ids"}).items():
            setattr(s, k, v)
        s.categories = [cats[n.lower()] for n in e.get("categories", []) if isinstance(n, str) and n.lower() in cats]
        s.next_fetch_at = utcnow()
        db.add(s)
        db.flush()
        created += 1
    return {"created": created, "skipped": skipped, "errors": errors}


def list_packs() -> list[dict]:
    out = []
    for path in sorted(PACK_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        meta = data.get("pack", {})
        sources = data.get("sources", [])
        out.append({
            "id": path.stem,
            "name": meta.get("name", path.stem),
            "description": meta.get("description"),
            "verified_at": meta.get("verified_at"),
            "notes": meta.get("notes"),
            "count": len(sources),
            "languages": sorted({s.get("language") for s in sources if s.get("language")}),
            "publishers": sorted({s.get("publisher") for s in sources if s.get("publisher")}),
        })
    return out


def load_pack(pack_id: str) -> list[dict] | None:
    if not pack_id.replace("-", "").replace("_", "").isalnum():
        return None
    path = PACK_DIR / f"{pack_id}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get("sources", [])
