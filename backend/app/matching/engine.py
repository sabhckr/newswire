"""Rule-based relevance scoring with an explainable breakdown.

This engine has no semantic understanding. It compares normalised keywords and phrases with
the headline and summary of each report, checks metadata (language, category, publisher,
geography, date) and adds configurable points. Every point awarded is recorded.

Keyword match levels (best one wins per keyword):
  exact       the phrase appears word-for-word (case/accents/punctuation ignored)   x1.0
  inflection  the phrase appears after light plural stripping (railways ~ railway)  x0.9
  tokens      every word of a multi-word phrase appears, not necessarily together   x0.6
  fuzzy       RapidFuzz partial ratio above the threshold (spelling variants)       x0.5
A match in the summary instead of the headline is multiplied by `body_factor`.
Required and excluded phrases only accept exact or inflection matches.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime

from rapidfuzz import fuzz

from app.core.text import contains_sequence, fold, stems, tokens
from app.geography.gazetteer import Gazetteer

KEYWORD_KINDS = ("required", "primary", "synonym", "secondary", "excluded")
GEO_ROLES = {"event", "article", "mentioned", "editor"}
ROLE_LABELS = {
    "event": "event location",
    "article": "feed location",
    "mentioned": "mentioned in text",
    "editor": "set by editor",
    "publisher": "publisher location",
}


@dataclass
class KeywordSpec:
    term: str
    kind: str
    weight: float | None = None
    toks: list[str] = field(default_factory=list)
    stms: list[str] = field(default_factory=list)
    folded: str = ""

    def __post_init__(self):
        self.folded = fold(self.term)
        self.toks = tokens(self.folded, folded=True)
        self.stms = stems(self.toks)


@dataclass
class AssignmentSpec:
    id: uuid.UUID | None
    headline: str
    keywords: list[KeywordSpec]
    min_score: float = 25.0
    language: str | None = None
    category_id: uuid.UUID | None = None
    category_name: str | None = None
    source_ids: set[uuid.UUID] = field(default_factory=set)
    source_names: dict[uuid.UUID, str] = field(default_factory=dict)
    restrict_to_sources: bool = False
    geo_targets: set[uuid.UUID] = field(default_factory=set)
    geo_names: list[str] = field(default_factory=list)
    geo_mode: str = "boost"
    start_date: date | None = None
    end_date: date | None = None

    def by_kind(self, kind: str) -> list[KeywordSpec]:
        return [k for k in self.keywords if k.kind == kind and k.toks]


@dataclass
class GeoFact:
    entity_id: uuid.UUID | None
    role: str
    confidence: float
    text: str | None
    name: str | None = None


@dataclass
class ItemView:
    id: uuid.UUID
    title: str
    body: str
    language: str | None
    category_text: str | None
    source_ids: set[uuid.UUID]
    source_category_ids: set[uuid.UUID]
    publisher_names: dict[uuid.UUID, str]
    published_at: datetime | None
    retrieved_at: datetime
    geo: list[GeoFact]
    title_toks: list[str] = field(default_factory=list)
    title_stms: list[str] = field(default_factory=list)
    body_toks: list[str] = field(default_factory=list)
    body_stms: list[str] = field(default_factory=list)
    folded_title: str = ""
    folded_body: str = ""

    def __post_init__(self):
        self.folded_title = fold(self.title)
        self.folded_body = fold(self.body)[:4000]
        self.title_toks = tokens(self.folded_title, folded=True)
        self.title_stms = stems(self.title_toks)
        self.body_toks = tokens(self.folded_body, folded=True)
        self.body_stms = stems(self.body_toks)


@dataclass
class Component:
    kind: str
    label: str
    points: float
    detail: str

    def as_dict(self) -> dict:
        return {"kind": self.kind, "label": self.label, "points": round(self.points, 2), "detail": self.detail}


@dataclass
class MatchResult:
    matched: bool
    score: float
    components: list[Component]
    explanation: str
    rejected_reason: str | None = None

    def breakdown(self) -> list[dict]:
        return [c.as_dict() for c in self.components]


@dataclass
class TermHit:
    factor: float
    field: str
    level: str


def find_term(kw: KeywordSpec, item: ItemView, opts: dict, *, allow_partial: bool) -> TermHit | None:
    best: TermHit | None = None
    fields = (
        ("headline", item.title_toks, item.title_stms, item.folded_title, 1.0),
        ("summary", item.body_toks, item.body_stms, item.folded_body, float(opts["body_factor"])),
    )
    for name, toks, stms, folded, field_factor in fields:
        if not toks:
            continue
        if contains_sequence(toks, kw.toks):
            factor, level = 1.0, "exact"
        elif contains_sequence(stms, kw.stms):
            factor, level = float(opts["inflection_factor"]), "inflection"
        elif allow_partial and len(kw.stms) > 1 and set(kw.stms) <= set(stms):
            factor, level = float(opts["token_factor"]), "tokens"
        elif (
            allow_partial
            and opts.get("fuzzy_enabled", True)
            and len(kw.folded) >= int(opts["fuzzy_min_length"])
            and fuzz.partial_ratio(kw.folded, folded) >= float(opts["fuzzy_threshold"])
        ):
            factor, level = float(opts["fuzzy_factor"]), "fuzzy"
        else:
            continue
        total = factor * field_factor
        if best is None or total > best.factor:
            best = TermHit(total, name, level)
    return best


def _primary_lang(code: str | None) -> str | None:
    return code.split("-")[0].lower() if code else None


def _age_label(hours: float) -> str:
    if hours < 1:
        return f"{max(1, int(hours * 60))} min ago"
    if hours < 48:
        return f"{int(hours)} h ago"
    return f"{int(hours / 24)} days ago"


def evaluate(
    spec: AssignmentSpec,
    item: ItemView,
    weights: dict,
    opts: dict,
    gaz: Gazetteer | None,
    now: datetime,
) -> MatchResult:
    comps: list[Component] = []

    def reject(reason: str) -> MatchResult:
        return MatchResult(False, 0.0, comps, f"Not matched: {reason}.", reason)

    item_time = item.published_at or item.retrieved_at
    if spec.start_date and item_time.date() < spec.start_date:
        return reject("published before the assignment start date")
    if spec.end_date and item_time.date() > spec.end_date:
        return reject("published after the assignment end date")

    if spec.restrict_to_sources and spec.source_ids and not (item.source_ids & spec.source_ids):
        return reject("not from a selected source")

    if spec.language:
        if item.language:
            if _primary_lang(item.language) != _primary_lang(spec.language):
                return reject(f"language is {item.language}, assignment requires {spec.language}")
            comps.append(Component("language", spec.language, float(weights["language"]), "language matches"))

    for kw in spec.by_kind("excluded"):
        hit = find_term(kw, item, opts, allow_partial=False)
        if hit:
            if opts.get("excluded_mode", "reject") == "reject":
                return reject(f"contains excluded phrase “{kw.term}”")
            pts = kw.weight if kw.weight is not None else float(weights["excluded_penalty"])
            comps.append(Component("excluded", kw.term, -abs(pts), f"excluded phrase in {hit.field}"))

    for kw in spec.by_kind("required"):
        hit = find_term(kw, item, opts, allow_partial=False)
        if not hit:
            return reject(f"required phrase “{kw.term}” is missing")
        pts = (kw.weight if kw.weight is not None else float(weights["required_phrase"])) * hit.factor
        comps.append(Component("required", kw.term, pts, f"required phrase, {hit.field}, {hit.level}"))

    strong_hit = any(c.kind == "required" for c in comps)
    any_hit = strong_hit
    for kind, weight_key, label in (
        ("primary", "primary", "primary keyword"),
        ("synonym", "synonym", "synonym"),
        ("secondary", "secondary", "secondary keyword"),
    ):
        for kw in spec.by_kind(kind):
            hit = find_term(kw, item, opts, allow_partial=True)
            if not hit:
                continue
            pts = (kw.weight if kw.weight is not None else float(weights[weight_key])) * hit.factor
            comps.append(Component(kind, kw.term, pts, f"{label}, {hit.field}, {hit.level}"))
            any_hit = True
            if kind in ("primary", "synonym"):
                strong_hit = True

    if not any_hit:
        return reject("no keyword or phrase matched")
    if (spec.by_kind("primary") or spec.by_kind("synonym")) and not strong_hit:
        return reject("only secondary keywords matched")

    if spec.geo_targets:
        roles = set(GEO_ROLES)
        if opts.get("geo_include_publisher"):
            roles.add("publisher")
        min_conf = float(opts["geo_min_confidence"])
        geo_hit: GeoFact | None = None
        for g in item.geo:
            if g.entity_id is None or g.role not in roles or g.confidence < min_conf:
                continue
            ancestors = set(gaz.ancestors(g.entity_id)) if gaz else {g.entity_id}
            if ancestors & spec.geo_targets:
                if geo_hit is None or g.confidence > geo_hit.confidence:
                    geo_hit = g
        if geo_hit:
            name = geo_hit.name or geo_hit.text or "location"
            comps.append(
                Component(
                    "geography", name, float(weights["geography"]),
                    f"{ROLE_LABELS.get(geo_hit.role, geo_hit.role)}, confidence {geo_hit.confidence:.2f}",
                )
            )
        elif spec.geo_mode == "require":
            return reject("no matching location found in the report or its metadata")

    if spec.category_id or spec.category_name:
        cat_hit = spec.category_id in item.source_category_ids if spec.category_id else False
        if not cat_hit and spec.category_name and item.category_text:
            cat_hit = contains_sequence(tokens(item.category_text), tokens(spec.category_name))
        if cat_hit:
            comps.append(Component("category", spec.category_name or "category", float(weights["category"]), "category matches"))

    shared_sources = item.source_ids & spec.source_ids
    if shared_sources:
        sid = next(iter(shared_sources))
        name = item.publisher_names.get(sid) or spec.source_names.get(sid) or "selected source"
        comps.append(Component("publisher", name, float(weights["selected_publisher"]), "selected publisher"))

    window = float(opts["recency_window_hours"])
    age_hours = max(0.0, (now - item_time).total_seconds() / 3600)
    recency = float(weights["recency_max"]) * max(0.0, 1.0 - age_hours / window) if window > 0 else 0.0
    if recency >= 0.5:
        basis = "published" if item.published_at else "retrieved (no publication time in feed)"
        comps.append(Component("recency", _age_label(age_hours), recency, f"{basis} {_age_label(age_hours)}"))

    score = round(sum(c.points for c in comps), 1)
    matched = score >= spec.min_score
    labels = []
    for c in comps:
        if c.points <= 0:
            continue
        if c.kind in ("required", "primary", "synonym", "secondary", "geography"):
            labels.append(c.label)
        elif c.kind == "publisher":
            labels.append(f"selected publisher {c.label}")
        elif c.kind == "category":
            labels.append(f"category {c.label}")
        elif c.kind == "recency":
            labels.append(f"published {c.label}")
    if matched:
        explanation = f"Matched because: {', '.join(labels)}. Score {score:g}."
    else:
        explanation = f"Below threshold ({score:g} < {spec.min_score:g}): {', '.join(labels) or 'weak evidence'}."
    return MatchResult(matched, score, comps, explanation, None if matched else "score below threshold")
