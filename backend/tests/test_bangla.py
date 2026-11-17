"""Bangla (Bengali) text handling: folding, inflection-aware matching, stopwords, clustering, slugs."""

from __future__ import annotations

import uuid

import pytest

from app.clustering.engine import make_sig, similarity
from app.core.db import utcnow
from app.core.text import (
    content_hash,
    contains_forms,
    fold,
    forms_list,
    significant_tokens,
    slugify,
    stem,
    tokens,
)
from app.matching.engine import AssignmentSpec, ItemView, KeywordSpec, evaluate
from app.services.settings import DEFAULTS

W = DEFAULTS["matching.weights"]
O = DEFAULTS["matching.options"]
CFG = DEFAULTS["clustering"]

ZWJ, ZWNJ = "‍", "‌"


def test_fold_removes_joiners_and_maps_bangla_digits():
    assert fold(f"র{ZWJ}্যাব") == fold(f"র{ZWNJ}্যাব") == fold("র্যাব")
    assert fold("১৭ নভেম্বর ২০২৬") == "17 নভেম্বর 2026"
    # Old-style khanda ta (ta + hasant + ZWJ) folds to the modern letter.
    assert fold("উৎসব") == fold(f"উত্{ZWJ}সব")
    # Bangla vowel signs are kept: only Latin accents are stripped.
    assert fold("সেতু") == "সেতু" and fold("Café") == "cafe"


def test_tokens_split_on_danda_and_keep_conjuncts():
    assert tokens("পদ্মা সেতুর উদ্বোধন। শিক্ষার্থীদের বিক্ষোভ") == ["পদ্মা", "সেতুর", "উদ্বোধন", "শিক্ষার্থীদের", "বিক্ষোভ"]


@pytest.mark.parametrize(
    "inflected,base",
    [
        ("ঢাকায়", "ঢাকা"),
        ("ঢাকার", "ঢাকা"),
        ("সেতুর", "সেতু"),
        ("সরকারের", "সরকার"),
        ("সরকারকে", "সরকার"),
        ("শিক্ষার্থীদের", "শিক্ষার্থী"),
        ("শিক্ষার্থীরা", "শিক্ষার্থী"),
        ("চট্টগ্রামে", "চট্টগ্রাম"),
        ("দোকানগুলো", "দোকান"),
    ],
)
def test_inflected_forms_match_the_base_word(inflected, base):
    assert contains_forms(forms_list([inflected]), forms_list([base]))


def test_short_words_are_not_over_stripped():
    # "কর" (do/tax) must not match "করে" just because "-ে" is a suffix.
    assert not contains_forms(forms_list(["করে"]), forms_list(["কর"]))
    assert stem("সময়") == "সময়"


def test_stems_used_for_clustering_agree():
    assert stem("চট্টগ্রামে") == stem("চট্টগ্রামের") == stem("চট্টগ্রামকে") == "চট্টগ্রাম"


def test_bangla_stopwords_are_not_significant():
    sig = significant_tokens("ঢাকায় এবং চট্টগ্রামে আজ থেকে হরতাল")
    assert "এবং" not in sig and "থেকে" not in sig and "আজ" not in sig
    assert {"ঢাকা", "চট্টগ্রাম", "হরতাল"} <= sig


def test_content_hash_ignores_joiner_and_digit_variants():
    assert content_hash(f"র{ZWJ}্যাবের অভিযান ২০২৬") == content_hash(f"র{ZWNJ}্যাবের অভিযান 2026")


def test_slugify_keeps_bangla_words():
    assert slugify("পদ্মা সেতুতে রেল চলাচল শুরু!") == "পদ্মা-সেতুতে-রেল-চলাচল-শুরু"
    assert slugify("।।।") == "article"


def _item(title, body=""):
    t = utcnow()
    return ItemView(
        id=uuid.uuid4(), title=title, body=body, language="bn", category_text=None,
        source_ids=set(), source_category_ids=set(), publisher_names={},
        published_at=t, retrieved_at=t, geo=[],
    )


def _primary(term, title, body=""):
    spec = AssignmentSpec(id=None, headline="t", keywords=[KeywordSpec(term, "primary")])
    r = evaluate(spec, _item(title, body), W, O, None, utcnow())
    return [c for c in r.components if c.kind == "primary"][0]


def test_matching_exact_and_inflected_bangla_phrase():
    exact = _primary("পদ্মা সেতু", "পদ্মা সেতু দিয়ে যান চলাচল বেড়েছে")
    assert "headline, exact" in exact.detail and exact.points == W["primary"]
    infl = _primary("পদ্মা সেতু", "পদ্মা সেতুর টোল আদায় ছাড়াল দুই হাজার কোটি")
    assert "headline, inflection" in infl.detail
    assert infl.points == pytest.approx(W["primary"] * O["inflection_factor"])


def test_matching_bangla_phrase_with_joiner_variant():
    c = _primary(f"র{ZWNJ}্যাব", f"র{ZWJ}্যাবের অভিযানে আটক ৫")
    assert c.points > 0


def test_bangla_headlines_from_two_papers_cluster():
    a = make_sig(uuid.uuid4(), "পদ্মা সেতুতে রেল চলাচল শুরু হচ্ছে আগামী মাসে", "", None, utcnow(), None)
    b = make_sig(uuid.uuid4(), "আগামী মাসেই পদ্মা সেতুতে শুরু হচ্ছে রেল চলাচল", "", None, utcnow(), None)
    assert similarity(a, b, CFG)[1] == "headline"


def test_unrelated_bangla_reports_sharing_a_place_do_not_cluster():
    a = make_sig(uuid.uuid4(), "ঢাকায় ডেঙ্গু রোগী বাড়ছে", "হাসপাতালে ভর্তি রোগীর সংখ্যা বেড়েছে", None, utcnow(), None)
    b = make_sig(uuid.uuid4(), "ঢাকার যানজট কমাতে নতুন পরিকল্পনা", "সড়কে গণপরিবহন বাড়ানো হবে", None, utcnow(), None)
    assert similarity(a, b, CFG) is None


# ---- Bangladesh gazetteer and datelines ---------------------------------------------------

from sqlalchemy import func, select  # noqa: E402

from app.geography.classify import classify, detect_dateline  # noqa: E402
from app.geography.gazetteer import get_gazetteer  # noqa: E402
from app.geography.seed import seed_bangladesh  # noqa: E402
from app.models import GeographicEntity, NewsItem, Source  # noqa: E402


def test_bangladesh_divisions_and_districts_are_seeded_once(db):
    counts = dict(db.execute(
        select(GeographicEntity.kind, func.count()).where(GeographicEntity.country_code == "BD")
        .group_by(GeographicEntity.kind)
    ).all())
    assert counts["admin1"] == 8 and counts["admin2"] == 64
    assert seed_bangladesh(db) == {"created": 0, "updated": 0, "missing": 0}


def test_bangladesh_names_are_unique_in_the_gazetteer(db):
    gaz = get_gazetteer(db)
    clashes = {
        key: [gaz.entities[e.entity_id].name for e in entries]
        for key, entries in gaz.by_name.items()
        if len(entries) > 1 and any(gaz.entities[e.entity_id].country_code == "BD" for e in entries)
    }
    assert clashes == {}


def _names(gaz, text):
    return [gaz.entities[m.entity_id].name for m in gaz.scan(text)]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("ঢাকায় সংবাদ সম্মেলন", ["Dhaka"]),
        ("চট্টগ্রাম বিভাগের ১১ জেলায়", ["Chattogram Division"]),
        ("বগুড়ায় সড়ক দুর্ঘটনা", ["Bogura District"]),
        ("গাজীপুর জেলা প্রশাসক", ["Gazipur District"]),
        ("Gazipur factory fire", ["Gazipur"]),
        ("Flooding in Jessore and Bogra", ["Jashore District", "Bogura District"]),
        ("কক্সবাজারের উখিয়ায় ক্যাম্পে আগুন", ["Cox's Bazar", "Ukhia"]),
        ("ভারতের প্রধানমন্ত্রী নয়াদিল্লিতে", ["India", "New Delhi"]),
        ("মিয়ানমারের সীমান্তে", ["Myanmar"]),
        # "ঢাকা" is also "covered": not a place here.
        ("ঘন কুয়াশায় ঢাকা পড়েছে উত্তরাঞ্চল", []),
        ("ঢাকা থেকে সিলেট", ["Dhaka", "Sylhet"]),
    ],
)
def test_bangla_and_bangladeshi_places_resolve(db, text, expected):
    assert _names(get_gazetteer(db), text) == expected


def test_bangladesh_hierarchy(db):
    gaz = get_gazetteer(db)
    ukhia = gaz.scan("উখিয়া")[0].entity_id
    assert [gaz.entities[e].name for e in gaz.ancestors(ukhia)] == [
        "Ukhia", "Cox's Bazar District", "Chattogram Division", "Bangladesh", "Asia",
    ]


@pytest.mark.parametrize(
    "text,place",
    [
        ("ঢাকা, ১৭ নভেম্বর (বাসস) : প্রধান উপদেষ্টা আজ", "ঢাকা"),
        ("চট্টগ্রাম, ১৭ নভেম্বর, ২০২৬ — বন্দরে কনটেইনার জট", "চট্টগ্রাম"),
        ("রাজশাহী প্রতিনিধি রাজশাহীতে আমের দাম", "রাজশাহী"),
        ("উখিয়া (কক্সবাজার) প্রতিনিধি ক্যাম্পে", "উখিয়া কক্সবাজার"),
        ("নিজস্ব প্রতিবেদক, সিলেট সিলেটে বন্যা", "সিলেট"),
        ("Staff Correspondent, Bogura A truck", "Bogura"),
        ("DHAKA, Nov 17, 2026 (BSS) - The cabinet", "DHAKA"),
        ("নিজস্ব প্রতিবেদক রাজধানীর মিরপুরে", None),
        ("ঢাকার যানজট নিয়ে আলোচনা", None),
    ],
)
def test_bangla_and_bangladeshi_datelines(text, place):
    assert detect_dateline(text) == place


def test_bangla_report_gets_event_and_mentioned_places(db):
    gaz = get_gazetteer(db)
    src = Source(name="S", publisher="P", feed_url="https://x.example/rss", country_code="BD")
    item = NewsItem(
        title="সিলেটে বন্যায় পানিবন্দি লাখো মানুষ",
        summary="সিলেট, ১৭ নভেম্বর (বাসস) : সুনামগঞ্জ ও সিলেটের নিম্নাঞ্চল প্লাবিত হয়েছে।",
        url="https://x.example/bn1",
    )
    roles = {(a.role, gaz.entities[a.entity_id].name if a.entity_id else None): a.confidence
             for a in classify(item, src, gaz)}
    assert roles[("publisher", "Bangladesh")] == 1.0
    assert roles[("event", "Sylhet")] >= 0.8
    assert roles[("mentioned", "Sylhet")] >= 0.5
    assert roles[("mentioned", "Sunamganj District")] >= 0.5


def test_analytics_days_follow_the_viewers_time_zone(db):
    from datetime import date, datetime, timezone

    from app.analytics.queries import date_range, news_volume

    # 20:00 UTC on 16 November is 02:00 on 17 November in Dhaka (UTC+6).
    t = datetime(2026, 11, 16, 20, 0, tzinfo=timezone.utc)
    db.add(NewsItem(title="রাতের খবর", url="https://x.example/night", normalized_url="https://x.example/night", content_hash="night",
                    retrieved_at=t, sort_at=t))
    db.commit()
    s, e = date_range(date(2026, 11, 10), date(2026, 11, 20), tz="Asia/Dhaka")
    assert s == datetime(2026, 11, 9, 18, 0, tzinfo=timezone.utc)
    assert [r["bucket"] for r in news_volume(db, s, e, tz="Asia/Dhaka")] == ["2026-11-17"]
    s, e = date_range(date(2026, 11, 10), date(2026, 11, 20))
    assert [r["bucket"] for r in news_volume(db, s, e)] == ["2026-11-16"]


def test_direction_marks_do_not_change_the_content_hash():
    assert content_hash("‎মিরপুরে বাসে আগুন") == content_hash("মিরপুরে বাসে আগুন")


# ---- Search ---------------------------------------------------------------------------------

from app.api.news import bangla_search_terms, bangla_tsquery, build_news_query  # noqa: E402

YA_SINGLE = "য়"  # য় as one character; fold() gives য + nukta


def test_bangla_search_terms_use_prefixes_stems_and_both_nukta_spellings():
    (neg, variants), = bangla_search_terms("ঢাকায়")
    words = dict(variants)
    assert not neg and words["ঢাকা"] is True  # the stem, as a prefix
    assert words[f"ঢাকা{YA_SINGLE}"] is True  # the typed word, single-character spelling
    assert bangla_tsquery("মেয়র -চট্টগ্রাম").endswith("!('চট্টগ্রাম':*)")
    assert "'১০'" in bangla_tsquery("১০ জন নিহত")  # Bangla digits as typed in feeds


def test_bangla_function_words_are_dropped_unless_alone():
    assert {v for _, vs in bangla_search_terms("সমাবেশ ও মিছিল") for v, _ in vs} == {"সমাবেশ", "মিছিল"}
    assert bangla_search_terms("ও")  # a query of only function words still searches
    assert bangla_tsquery("-ঢাকা") is None  # nothing to look for


def test_bangla_search_finds_inflected_forms_and_both_spellings(db):
    now = utcnow()
    titles = {
        "a": f"ঢাকা{YA_SINGLE} আগুনে পুড়ল দোকান",  # single-character য়
        "b": "ঢাকার মেয়র নির্বাচনে নতুন প্রার্থী",
        "c": "চট্টগ্রামের মেয়র বললেন",
        "d": "Dhaka traffic",
    }
    for key, title in titles.items():
        db.add(NewsItem(title=title, url=f"https://x.example/{key}", normalized_url=f"https://x.example/{key}",
                        content_hash=f"h-{key}", retrieved_at=now, sort_at=now))
    db.commit()

    def hits(q):
        return {n.url.rsplit("/", 1)[1] for n in db.scalars(build_news_query(db, q=q))}

    assert hits("ঢাকা") == {"a", "b"}
    assert hits("ঢাকায়") == {"a", "b"}  # typed with য + nukta, stored as one character
    assert hits("আগুন") == {"a"}
    assert hits("মেয়র") == {"b", "c"}
    assert hits("মেয়র -চট্টগ্রাম") == {"b"}
    assert hits("Dhaka") == {"d"}
