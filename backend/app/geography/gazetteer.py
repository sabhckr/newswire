"""In-memory gazetteer built from `geographic_entities` for deterministic place detection."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.text import _QUOTES, _is_word_char, fold
from app.models import GeographicEntity

# Place names that are also everyday English words: counted only when capitalised and
# always with low confidence.
COMMON_WORD_PLACES = frozenset(
    {
        "split", "nice", "cork", "mobile", "reading", "bath", "police", "male", "victoria",
        "march", "bar", "born", "most", "of", "deal", "sale", "wells", "university", "union",
        "independence", "mission", "hope", "liberty", "commerce", "enterprise", "progress",
        "opportunity", "justice", "beach", "lagoon", "garden", "temple", "ocean", "sandy",
        "chester", "aden", "kandy", "salvador", "georgetown", "kingston", "santiago", "cali",
    }
)


@dataclass
class EntityInfo:
    id: uuid.UUID
    name: str
    kind: str
    country_code: str | None
    parent_id: uuid.UUID | None
    is_capital: bool = False
    member_ids: list[uuid.UUID] = field(default_factory=list)


@dataclass
class NameEntry:
    entity_id: uuid.UUID
    ambiguous: bool = False
    demonym: bool = False


@dataclass
class Mention:
    entity_id: uuid.UUID
    text: str
    ambiguous: bool
    demonym: bool
    candidates: int  # how many entities share this name


def raw_tokens(text: str) -> list[tuple[str, str]]:
    """Tokens with their original casing preserved: [(raw, folded), ...]."""
    if not text:
        return []
    s = text.translate(_QUOTES)
    out: list[tuple[str, str]] = []
    buf: list[str] = []

    def flush():
        if buf:
            raw = "".join(buf)
            out.append((raw, fold(raw)))
            buf.clear()

    for ch in s:
        if _is_word_char(ch):
            buf.append(ch)
        else:
            flush()
    flush()
    return out


class Gazetteer:
    def __init__(self, rows: list[GeographicEntity]):
        self.entities: dict[uuid.UUID, EntityInfo] = {}
        self.by_name: dict[tuple[str, ...], list[NameEntry]] = {}
        self.exact: dict[tuple[str, ...], list[NameEntry]] = {}
        self.countries: dict[str, uuid.UUID] = {}
        self.max_len = 1
        for row in rows:
            alts = list(row.alt_names or [])
            info = EntityInfo(
                id=row.id,
                name=row.name,
                kind=row.kind,
                country_code=row.country_code,
                parent_id=row.parent_id,
                is_capital="@capital" in alts,
                member_ids=[uuid.UUID(str(m)) for m in (row.member_ids or [])],
            )
            self.entities[row.id] = info
            if row.kind == "country" and row.country_code:
                self.countries[row.country_code.upper()] = row.id
            self._add(row.name, row.id, ambiguous=row.is_ambiguous)
            for alt in alts:
                if alt.startswith("@"):
                    continue  # internal markers such as @capital
                demonym = alt.startswith("#")
                alt = alt.lstrip("#")
                if alt.startswith("="):
                    key = tuple(r for r, _ in raw_tokens(alt[1:]))
                    if key:
                        self.exact.setdefault(key, []).append(NameEntry(row.id, False, demonym))
                        self.max_len = max(self.max_len, len(key))
                    continue
                amb = row.is_ambiguous or alt.startswith("~")
                self._add(alt.lstrip("~"), row.id, ambiguous=amb, demonym=demonym)

    def _add(self, name: str, entity_id: uuid.UUID, *, ambiguous: bool, demonym: bool = False) -> None:
        key = tuple(f for _, f in raw_tokens(name))
        if not key:
            return
        if len(key) == 1 and key[0] in COMMON_WORD_PLACES:
            ambiguous = True
        entries = self.by_name.setdefault(key, [])
        if not any(e.entity_id == entity_id for e in entries):
            entries.append(NameEntry(entity_id, ambiguous, demonym))
        self.max_len = max(self.max_len, len(key))

    # --- hierarchy -------------------------------------------------------------------
    def ancestors(self, entity_id: uuid.UUID) -> list[uuid.UUID]:
        out: list[uuid.UUID] = []
        seen: set[uuid.UUID] = set()
        cur: uuid.UUID | None = entity_id
        while cur is not None and cur not in seen:
            seen.add(cur)
            out.append(cur)
            info = self.entities.get(cur)
            cur = info.parent_id if info else None
        return out

    def country_id(self, code: str | None) -> uuid.UUID | None:
        return self.countries.get(code.upper()) if code else None

    def country_of(self, entity_id: uuid.UUID) -> str | None:
        info = self.entities.get(entity_id)
        return info.country_code if info else None

    def expand_targets(self, entity_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        """Assignment targets, with custom areas expanded into their member entities."""
        out: set[uuid.UUID] = set()
        for eid in entity_ids:
            out.add(eid)
            info = self.entities.get(eid)
            if info and info.member_ids:
                out.update(info.member_ids)
        return out

    # --- detection -------------------------------------------------------------------
    def scan(self, text: str | None) -> list[Mention]:
        """Longest-match-first scan of a text for known place names."""
        toks = raw_tokens(text or "")
        n = len(toks)
        out: list[Mention] = []
        i = 0
        while i < n:
            matched = False
            for length in range(min(self.max_len, n - i), 0, -1):
                raw_key = tuple(r for r, _ in toks[i : i + length])
                entries = self.exact.get(raw_key)
                folded_key = tuple(f for _, f in toks[i : i + length])
                if entries is None:
                    entries = self.by_name.get(folded_key)
                if not entries:
                    continue
                first_raw = toks[i][0]
                capitalised = first_raw[:1].isupper() or not first_raw[:1].isalpha() or not first_raw.isascii()
                usable = [e for e in entries if not e.ambiguous or capitalised]
                if not usable:
                    continue
                span = " ".join(raw_key)
                for e in usable:
                    out.append(Mention(e.entity_id, span, e.ambiguous, e.demonym, len(usable)))
                i += length
                matched = True
                break
            if not matched:
                i += 1
        return out

    def resolve(self, text: str | None) -> list[uuid.UUID]:
        """Entities named by a short location string (feed metadata, category label)."""
        return [m.entity_id for m in self.scan(text)]


_cache_lock = threading.Lock()
_cache: dict[str, object] = {"key": None, "gaz": None, "at": 0.0}


def get_gazetteer(db: Session, *, max_age_seconds: float = 300.0) -> Gazetteer:
    key = db.execute(
        select(func.count(GeographicEntity.id), func.max(GeographicEntity.updated_at))
    ).one()
    key = (key[0], str(key[1]))
    with _cache_lock:
        fresh = time.monotonic() - float(_cache["at"]) < max_age_seconds  # type: ignore[arg-type]
        if _cache["gaz"] is not None and _cache["key"] == key and fresh:
            return _cache["gaz"]  # type: ignore[return-value]
    rows = list(db.scalars(select(GeographicEntity)))
    gaz = Gazetteer(rows)
    with _cache_lock:
        _cache.update(key=key, gaz=gaz, at=time.monotonic())
    return gaz


def invalidate_gazetteer() -> None:
    with _cache_lock:
        _cache.update(key=None, gaz=None, at=0.0)
