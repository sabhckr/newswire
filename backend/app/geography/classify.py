"""Attach places to news items with explicit roles, provenance and confidence.

Roles are kept apart on purpose:
  publisher  - where the outlet is based (from source settings), never the event location
  event      - where the event happened: a wire-style dateline, or set by an editor
  article    - location supplied by the feed itself (geo tags, location field, place categories)
  mentioned  - a place named in the headline or summary

Unknown stays unknown: nothing is inferred beyond what the text or metadata states.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.geography.gazetteer import Gazetteer, Mention
from app.models import NewsGeography, NewsItem, Source

# "DHAKA, Nov 17 (Reuters) -", "WASHINGTON (AP) —", "Dhaka, 17 November:", "KYIV, Ukraine —"
# Bangla datelines and correspondent credits are handled further down.
_DATELINE = re.compile(
    r"^\s*(?P<place>[A-Z][A-Za-zÀ-ɏ'.\- ]{1,40}?)"
    r"(?:,\s*(?P<qual>[A-Z][A-Za-zÀ-ɏ'. ]{1,30}?))?"
    r"(?:,?\s*(?:\d{1,2}\s+[A-Z][a-z]{2,8}|[A-Z][a-z]{2,8}\.?\s+\d{1,2})(?:,?\s*\d{4})?)?"
    r"\s*(?:\((?:[A-Za-z .&-]{2,30})\))?\s*[—–:\-]{1,2}\s"
)


_BN = "\u0980-\u09ff"
_BN_WORDS = rf"[{_BN}]+(?:[ \-][{_BN}]+){{0,2}}?"
_BN_MONTHS = "জানুয়ারি|জানুয়ারী|ফেব্রুয়ারি|ফেব্রুয়ারী|মার্চ|এপ্রিল|মে|জুন|জুলাই|আগস্ট|আগষ্ট|সেপ্টেম্বর|অক্টোবর|নভেম্বর|ডিসেম্বর"
# "ঢাকা, ১৭ নভেম্বর (বাসস) :", "চট্টগ্রাম, ১৭ নভেম্বর, ২০২৬ —"
_BN_DATELINE = re.compile(
    rf"^\s*(?P<place>{_BN_WORDS})\s*,\s*\d{{1,2}}\s*(?:{_BN_MONTHS})(?:\s*,?\s*\d{{4}})?"
    rf"\s*(?:\([^)]{{1,30}}\))?\s*[:—–\-]"
)
# Correspondent credits: "রাজশাহী প্রতিনিধি", "উখিয়া (কক্সবাজার) প্রতিনিধি",
# "নিজস্ব প্রতিবেদক, সিলেট", "Staff Correspondent, Bogura", "Our Correspondent, Cumilla".
_BN_CORRESPONDENT = re.compile(
    rf"^\s*(?:(?P<place>{_BN_WORDS})(?:\s*\((?P<qual>[{_BN} ]{{2,30}})\))?\s+(?:প্রতিনিধি|সংবাদদাতা)(?=[\s,:।—–\-]|$)"
    rf"|(?:নিজস্ব|স্টাফ|বিশেষ|জেলা)\s+(?:প্রতিবেদক|সংবাদদাতা|প্রতিনিধি)\s*,\s*(?P<place2>{_BN_WORDS})(?=[\s,:।—–\-]|$))"
)
_EN_CORRESPONDENT = re.compile(
    r"^\s*(?:Staff|Our|Own|Special|District|Local)\s+(?:Correspondent|Reporter)s?\s*,\s*"
    r"(?P<place>[A-Z][A-Za-z'’.\-]+(?:\s[A-Z][A-Za-z'’.\-]+){0,2})"
)
_TRANSLATE = str.maketrans({"\u200c": None, "\u200d": None, **{chr(0x09E6 + i): str(i) for i in range(10)}})


def _detect_bangla(text: str) -> str | None:
    head = text[:160].translate(_TRANSLATE)
    m = _BN_DATELINE.match(head)
    if m:
        return m.group("place").strip()
    m = _BN_CORRESPONDENT.match(head)
    if m:
        if m.group("place2"):
            return m.group("place2").strip()
        place, qual = m.group("place").strip(), (m.group("qual") or "").strip()
        return f"{place} {qual}".strip()
    return None


def detect_dateline(text: str | None) -> str | None:
    if not text:
        return None
    if any("\u0980" <= ch <= "\u09ff" for ch in text[:40]):
        return _detect_bangla(text)
    m = _EN_CORRESPONDENT.match(text[:160])
    if m:
        return m.group("place").strip()
    m = _DATELINE.match(text[:160])
    if not m:
        return None
    place = m.group("place").strip()
    # Datelines are written in capitals or as a capitalised place followed by a comma/date.
    if not (place.isupper() or m.group("qual") or "," in text[: len(place) + 2]):
        return None
    if len(place.split()) > 4:
        return None
    return place


@dataclass
class GeoAssignment:
    entity_id: uuid.UUID | None
    role: str
    method: str
    confidence: float
    original_text: str | None


def _confidence_for(m: Mention, gaz: Gazetteer, *, in_headline: bool, context_countries: set[str]) -> float:
    info = gaz.entities.get(m.entity_id)
    if info is None:
        return 0.0
    if info.kind in ("country", "continent", "custom"):
        conf = 0.9 if in_headline else 0.75
        if m.demonym:
            conf = 0.65 if in_headline else 0.55
    else:
        country = info.country_code
        if country and country in context_countries:
            conf = 0.85
        elif info.is_capital:
            conf = 0.65
        elif info.kind == "admin1":
            conf = 0.6
        else:
            conf = 0.55
    if m.candidates > 1:
        conf = 0.85 if (info.country_code and info.country_code in context_countries) else 0.3
    if m.ambiguous:
        conf = min(conf, 0.45)
    return round(conf, 2)


def classify(item: NewsItem, source: Source | None, gaz: Gazetteer) -> list[GeoAssignment]:
    found: dict[tuple[str, uuid.UUID | None, str | None], GeoAssignment] = {}

    def add(a: GeoAssignment) -> None:
        key = (a.role, a.entity_id, None if a.entity_id else a.original_text)
        prev = found.get(key)
        if prev is None or a.confidence > prev.confidence:
            found[key] = a

    # Publisher location: from the source configuration only.
    if source is not None and source.country_code:
        text = ", ".join(p for p in (source.city, source.region, source.country_code) if p)
        add(GeoAssignment(gaz.country_id(source.country_code), "publisher", "source_metadata", 1.0, text))

    # Feed-supplied location metadata.
    if item.location_text:
        ids = gaz.resolve(item.location_text)
        if ids:
            for eid in ids:
                add(GeoAssignment(eid, "article", "feed_metadata", 0.9 if len(ids) == 1 else 0.5, item.location_text))
        else:
            add(GeoAssignment(None, "article", "feed_metadata", 0.0, item.location_text[:300]))
    for cat in (item.extra or {}).get("categories", [])[:20]:
        for m in gaz.scan(cat):
            info = gaz.entities.get(m.entity_id)
            if info and not m.demonym:
                add(GeoAssignment(m.entity_id, "article", "feed_category", 0.8 if m.candidates == 1 else 0.4, cat[:300]))

    head = gaz.scan(item.title)
    body = gaz.scan(item.summary)
    context = {
        gaz.entities[m.entity_id].country_code
        for m in head + body
        if m.entity_id in gaz.entities
        and gaz.entities[m.entity_id].kind == "country"
        and gaz.entities[m.entity_id].country_code
        and m.candidates == 1
        and not m.ambiguous
    }

    # Event location from a dateline at the start of the summary or content.
    dateline = detect_dateline(item.summary) or detect_dateline(item.content)
    if dateline:
        mentions = gaz.scan(dateline.title() if dateline.isupper() else dateline)
        for m in mentions:
            conf = 0.8 if m.candidates == 1 else (0.7 if gaz.country_of(m.entity_id) in context else 0.35)
            add(GeoAssignment(m.entity_id, "event", "dateline", conf, dateline))

    for m in head:
        add(GeoAssignment(m.entity_id, "mentioned", "text_match",
                          _confidence_for(m, gaz, in_headline=True, context_countries=context), m.text))
    for m in body:
        add(GeoAssignment(m.entity_id, "mentioned", "text_match",
                          _confidence_for(m, gaz, in_headline=False, context_countries=context), m.text))
    return list(found.values())


def persist(db: Session, item: NewsItem, assignments: list[GeoAssignment], gaz: Gazetteer) -> list[NewsGeography]:
    rows = []
    for a in assignments:
        info = gaz.entities.get(a.entity_id) if a.entity_id else None
        country = info.country_code if info else None
        city = info.name if info and info.kind == "city" else None
        region = None
        if info and info.kind in ("admin1", "admin2"):
            region = info.name
        elif info and info.kind == "city" and info.parent_id:
            parent = gaz.entities.get(info.parent_id)
            if parent and parent.kind in ("admin1", "admin2"):
                region = parent.name
        row = NewsGeography(
            news_item_id=item.id,
            entity_id=a.entity_id,
            role=a.role,
            method=a.method,
            confidence=a.confidence,
            original_text=(a.original_text or "")[:300] or None,
            country_code=country,
            region=region,
            city=city,
        )
        db.add(row)
        rows.append(row)
    return rows


def classify_and_store(db: Session, item: NewsItem, source: Source | None, gaz: Gazetteer) -> list[NewsGeography]:
    return persist(db, item, classify(item, source, gaz), gaz)
