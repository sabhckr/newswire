"""Unit tests for keyword/fuzzy matching, scoring, geography and explanations."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from app.core.db import utcnow
from app.geography.gazetteer import get_gazetteer
from app.matching.engine import AssignmentSpec, GeoFact, ItemView, KeywordSpec, evaluate
from app.services.settings import DEFAULTS

W = DEFAULTS["matching.weights"]
O = DEFAULTS["matching.options"]


def item(title, body="", *, language="en", sources=None, geo=None, hours_ago=1.0, published=True, category_text=None):
    now = utcnow()
    t = now - timedelta(hours=hours_ago)
    return ItemView(
        id=uuid.uuid4(), title=title, body=body, language=language, category_text=category_text,
        source_ids=set(sources or ()), source_category_ids=set(), publisher_names={},
        published_at=t if published else None, retrieved_at=t, geo=geo or [],
    )


def spec(*kws, **kw):
    return AssignmentSpec(id=None, headline="test", keywords=[KeywordSpec(t, k) for k, t in kws], **kw)


def run(s, i, gaz=None):
    return evaluate(s, i, W, O, gaz, utcnow())


def test_exact_primary_in_headline_scores_full_weight():
    r = run(spec(("primary", "railway")), item("Bangladesh railway project approved"))
    kw = [c for c in r.components if c.kind == "primary"][0]
    assert kw.points == W["primary"] and "headline, exact" in kw.detail
    assert r.matched and r.explanation.startswith("Matched because: railway")


def test_inflection_and_body_factor():
    r = run(spec(("primary", "railway")), item("Cabinet meets", "New railways announced"))
    kw = [c for c in r.components if c.kind == "primary"][0]
    assert kw.points == pytest.approx(W["primary"] * O["inflection_factor"] * O["body_factor"])
    assert "summary, inflection" in kw.detail


def test_token_match_for_multiword_phrase():
    r = run(spec(("primary", "development project")), item("Project for rail development unveiled"))
    kw = [c for c in r.components if c.kind == "primary"][0]
    assert "tokens" in kw.detail and kw.points == pytest.approx(W["primary"] * O["token_factor"])


def test_fuzzy_match_catches_spelling_variant():
    r = run(spec(("primary", "Padma Bridge")), item("Traffic on Padma Brigde doubles"))
    kw = [c for c in r.components if c.kind == "primary"][0]
    assert "fuzzy" in kw.detail


def test_required_phrase_missing_rejects():
    r = run(spec(("primary", "railway"), ("required", "Bangladesh")), item("India railway expansion"))
    assert not r.matched and "required phrase" in r.rejected_reason


def test_required_phrase_does_not_accept_fuzzy():
    r = run(spec(("required", "Bangladesh")), item("Bangladeshi rail link"))
    assert not r.matched


def test_excluded_phrase_rejects_or_penalises():
    s = spec(("primary", "railway"), ("excluded", "model railway"))
    i = item("Model railway exhibition opens")
    assert not run(s, i).matched
    opts = {**O, "excluded_mode": "penalty"}
    r = evaluate(s, i, W, opts, None, utcnow())
    assert any(c.kind == "excluded" and c.points < 0 for c in r.components)


def test_only_secondary_keywords_is_not_enough():
    r = run(spec(("primary", "railway"), ("secondary", "funding")), item("Funding for schools"))
    assert not r.matched and r.rejected_reason == "only secondary keywords matched"


def test_language_filter():
    s = spec(("primary", "railway"), language="en")
    assert not run(s, item("railway news", language="bn")).matched
    assert any(c.kind == "language" for c in run(s, item("railway news", language="en-GB")).components)
    unknown = run(s, item("railway news", language=None))
    assert unknown.matched and not any(c.kind == "language" for c in unknown.components)


def test_source_restriction_and_publisher_bonus():
    sid = uuid.uuid4()
    s = spec(("primary", "railway"), source_ids={sid}, restrict_to_sources=True)
    assert not run(s, item("railway news")).matched
    r = run(s, item("railway news", sources=[sid]))
    assert any(c.kind == "publisher" for c in r.components)


def test_recency_decays_and_unknown_publication_time_is_disclosed():
    fresh = run(spec(("primary", "railway")), item("railway", hours_ago=0.1))
    old = run(spec(("primary", "railway")), item("railway", hours_ago=100))
    assert any(c.kind == "recency" for c in fresh.components)
    assert not any(c.kind == "recency" for c in old.components)
    r = run(spec(("primary", "railway")), item("railway", published=False))
    rec = [c for c in r.components if c.kind == "recency"][0]
    assert "no publication time" in rec.detail


def test_date_range():
    s = spec(("primary", "railway"), start_date=date.today() + timedelta(days=1))
    assert not run(s, item("railway")).matched


def test_threshold():
    s = spec(("secondary", "railway"), min_score=100)
    r = run(s, item("railway"))
    assert not r.matched and r.explanation.startswith("Below threshold")


def test_geography_uses_hierarchy_and_ignores_publisher_location(db):
    gaz = get_gazetteer(db)
    bd = gaz.country_id("BD")
    dhaka = next(e.id for e in gaz.entities.values() if e.name == "Dhaka")
    asia = next(e.id for e in gaz.entities.values() if e.name == "Asia")
    s = spec(("primary", "railway"), geo_targets={asia}, geo_mode="require")
    city_item = item("railway news", geo=[GeoFact(dhaka, "mentioned", 0.85, "Dhaka", "Dhaka")])
    r = run(s, city_item, gaz)
    assert r.matched and any(c.kind == "geography" and c.label == "Dhaka" for c in r.components)

    pub_only = item("railway news", geo=[GeoFact(bd, "publisher", 1.0, "BD", "Bangladesh")])
    assert not run(s, pub_only, gaz).matched, "publisher location must not count as event location"

    low_conf = item("railway news", geo=[GeoFact(bd, "mentioned", 0.3, "x", "Bangladesh")])
    assert not run(s, low_conf, gaz).matched
    boost = spec(("primary", "railway"), geo_targets={bd})
    assert run(boost, low_conf, gaz).matched  # boost mode never rejects


def test_explanation_lists_reasons_in_order():
    sid = uuid.uuid4()
    s = spec(("primary", "railway"), ("secondary", "development project"), source_ids={sid})
    s.source_names = {sid: "Daily Star"}
    i = item("Railway development project announced", sources=[sid])
    i.publisher_names = {sid: "The Daily Star"}
    r = run(s, i)
    assert "railway" in r.explanation and "development project" in r.explanation
    assert "selected publisher The Daily Star" in r.explanation
