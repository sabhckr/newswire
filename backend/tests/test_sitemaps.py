"""News sitemaps as sources (publishers without RSS), sitemap indexes and feed discovery."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.collectors.discover import discover_feeds
from app.collectors.ingest import collect_source, pick_child_sitemap, probe_source_url
from app.collectors.parsers import FeedParseError, parse_sitemap
from app.models import NewsItem, Source
from tests.conftest import rss

SITE = "https://bn.example.com"
XML = {"content-type": "application/xml"}


def news_sitemap(items: list[tuple[str, str, str]]) -> str:
    urls = "".join(
        f"""<url><loc>{loc}</loc>
  <news:news>
    <news:publication><news:name>Example Bangla</news:name><news:language>bn</news:language></news:publication>
    <news:publication_date>{date}</news:publication_date>
    <news:title>{title}</news:title>
    <news:keywords>বাংলাদেশ, বন্যা</news:keywords>
  </news:news>
  <image:image><image:loc>{loc}.jpg</image:loc></image:image>
</url>"""
        for loc, title, date in items
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9" '
        'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">' + urls + "</urlset>"
    )


ITEMS = [
    (f"{SITE}/bangladesh/a1", "সিলেটে বন্যায় পানিবন্দি লাখো মানুষ", "2026-11-17T10:00:00+06:00"),
    (f"{SITE}/bangladesh/a2", "ঢাকায় মেট্রোরেলের নতুন স্টেশন চালু", "2026-11-17T09:30:00+06:00"),
]


def make_source(db, url, **kw) -> Source:
    s = Source(name="Example Bangla", publisher="Example Bangla", feed_url=url,
               source_type=kw.pop("source_type", "sitemap"), country_code="BD", polling_interval_seconds=600, **kw)
    db.add(s)
    db.commit()
    return s


def test_parse_news_sitemap():
    parsed = parse_sitemap(news_sitemap(ITEMS).encode())
    assert parsed.feed_format == "news_sitemap" and parsed.language == "bn"
    e = parsed.entries[0]
    assert e.title == "সিলেটে বন্যায় পানিবন্দি লাখো মানুষ"
    assert e.published_at.isoformat() == "2026-11-17T04:00:00+00:00"
    assert e.categories == ["বাংলাদেশ", "বন্যা"] and e.image_urls == [f"{SITE}/bangladesh/a1.jpg"]


def test_a_headline_with_a_bare_ampersand_does_not_lose_the_sitemap():
    """Seen on a live news sitemap: one unescaped "&" in a title made the whole file unreadable."""
    good = news_sitemap(ITEMS)
    title = ITEMS[0][1]
    broken = good.replace(title, title + " PR & Media \x07&amp; R&D &#2453;", 1)
    parsed = parse_sitemap(broken.encode())
    assert len(parsed.entries) == len(ITEMS)
    assert parsed.entries[0].title == title + " PR & Media & R&D ক"
    assert [e.title for e in parsed.entries[1:]] == [t for _, t, *_ in ITEMS[1:]]
    # Something that is really not XML is still reported as such.
    with pytest.raises(FeedParseError, match="Malformed sitemap"):
        parse_sitemap(good.replace("</urlset>", "").encode())
    with pytest.raises(FeedParseError, match="Malformed sitemap"):
        parse_sitemap(b"<urlset><url><loc>https://x.example/a</url></urlset>")


def test_sitemap_with_dtd_is_refused():
    bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><urlset><url><loc>&a;</loc></url></urlset>'
    with pytest.raises(FeedParseError, match="DTD"):
        parse_sitemap(bomb.encode())


def test_pick_child_sitemap_prefers_news_then_newest():
    from datetime import datetime, timezone

    d = lambda day: datetime(2026, 11, day, tzinfo=timezone.utc)  # noqa: E731
    assert pick_child_sitemap([(f"{SITE}/s-1.xml", d(16)), (f"{SITE}/s-2.xml", d(17))]) == f"{SITE}/s-2.xml"
    assert pick_child_sitemap([(f"{SITE}/s-2.xml", d(17)), (f"{SITE}/news.xml", None)]) == f"{SITE}/news.xml"
    assert pick_child_sitemap([]) is None


def test_news_sitemap_source_collects_bangla_items(db, feeds):
    feeds.add(f"{SITE}/news-sitemap.xml", news_sitemap(ITEMS), headers=XML)
    source = make_source(db, f"{SITE}/news-sitemap.xml")
    result = collect_source(db, source, client_factory=feeds.factory())
    assert result.log.status == "success" and result.log.items_new == 2
    item = db.scalar(select(NewsItem).where(NewsItem.url == f"{SITE}/bangladesh/a1"))
    assert item.language == "bn" and item.published_at is not None
    # Collected twice: nothing new.
    again = collect_source(db, source, client_factory=feeds.factory())
    assert again.log.items_new == 0 and again.log.items_duplicate == 2


def test_sitemap_index_is_followed_to_newest_news_sitemap(db, feeds):
    index = (
        '<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<sitemap><loc>{SITE}/sitemap/news-2026-11-16.xml</loc><lastmod>2026-11-16T23:00:00Z</lastmod></sitemap>"
        f"<sitemap><loc>{SITE}/sitemap/news-2026-11-17.xml</loc><lastmod>2026-11-17T10:00:00Z</lastmod></sitemap>"
        f"<sitemap><loc>{SITE}/sitemap/pages.xml</loc><lastmod>2026-11-17T11:00:00Z</lastmod></sitemap>"
        "</sitemapindex>"
    )
    feeds.add(f"{SITE}/sitemap-index.xml", index, headers={**XML, "etag": '"idx"'})
    feeds.add(f"{SITE}/sitemap/news-2026-11-17.xml", news_sitemap(ITEMS), headers=XML)
    source = make_source(db, f"{SITE}/sitemap-index.xml")
    result = collect_source(db, source, client_factory=feeds.factory())
    assert result.log.items_new == 2
    assert "news-2026-11-17.xml" in result.log.error_message
    db.refresh(source)
    assert source.etag is None, "the index's ETag must not be reused for the child sitemap"


def _plain_sitemap(*urls_and_ages) -> str:
    rows = "".join(
        f"<url><loc>{u}</loc><lastmod>{(utcnow() - age).strftime('%Y-%m-%dT%H:%M:%SZ')}</lastmod></url>"
        for u, age in urls_and_ages
    )
    return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{rows}</urlset>'


HTML = {"content-type": "text/html; charset=utf-8"}


def test_plain_sitemap_uses_page_title_when_metadata_lookup_is_on(db, feeds):
    feeds.add(f"{SITE}/sitemap.xml", _plain_sitemap((f"{SITE}/news/1", timedelta(hours=1))), headers=XML)
    feeds.add(f"{SITE}/news/1", '<html lang="bn"><head><meta property="og:title" content="রাজশাহীতে আমের দাম কমেছে">'
              '<meta property="og:type" content="article">'
              '<meta property="og:description" content="রাজশাহী প্রতিনিধি"></head><body></body></html>', headers=HTML)
    off = make_source(db, f"{SITE}/sitemap.xml")
    assert collect_source(db, off, client_factory=feeds.factory()).log.items_skipped == 1
    off.extract_page_metadata = True
    db.commit()
    result = collect_source(db, off, client_factory=feeds.factory())
    assert result.log.items_new == 1
    item = db.scalar(select(NewsItem).where(NewsItem.url == f"{SITE}/news/1"))
    assert item.title == "রাজশাহীতে আমের দাম কমেছে" and item.language == "bn"


def test_probe_detects_sitemap_even_when_type_is_rss(feeds):
    feeds.add(f"{SITE}/news-sitemap.xml", news_sitemap(ITEMS), headers=XML)
    res = probe_source_url(f"{SITE}/news-sitemap.xml", "rss", client_factory=feeds.factory())
    assert res["ok"] and res["feed_format"] == "news_sitemap" and res["entries"] == 2


def test_discover_finds_advertised_feeds_and_news_sitemaps(feeds, monkeypatch):
    monkeypatch.setattr("app.collectors.discover.time.sleep", lambda s: None)
    feeds.add(f"{SITE}/", '<html><head><title>Example Bangla</title>'
              '<link rel="alternate" type="application/rss+xml" title="সর্বশেষ" href="/feed/"></head></html>',
              headers={"content-type": "text/html"})
    feeds.add(f"{SITE}/robots.txt", f"User-agent: *\nAllow: /\nSitemap: {SITE}/news-sitemap.xml\nSitemap: {SITE}/sitemap.xml\n",
              headers={"content-type": "text/plain"})
    feeds.add(f"{SITE}/feed/", rss([{"title": "সর্বশেষ খবর", "link": f"{SITE}/x", "pubDate": "Tue, 17 Nov 2026 03:00:00 GMT"}]))
    feeds.add(f"{SITE}/news-sitemap.xml", news_sitemap(ITEMS), headers=XML)
    out = discover_feeds(f"{SITE}/", client_factory=feeds.factory())
    assert out["site_title"] == "Example Bangla"
    got = {(c["url"], c["source_type"], c["ok"]) for c in out["candidates"]}
    assert got == {(f"{SITE}/feed/", "rss", True), (f"{SITE}/news-sitemap.xml", "sitemap", True)}
    # The plain sitemap is not offered: only news/feed sitemaps from robots.txt are candidates.
    assert out["candidates"][0]["latest"] >= out["candidates"][1]["latest"]


def test_discover_falls_back_to_conventional_paths(feeds, monkeypatch):
    monkeypatch.setattr("app.collectors.discover.time.sleep", lambda s: None)
    feeds.add(f"{SITE}/", "<html><head><title>No links</title></head></html>", headers={"content-type": "text/html"})
    feeds.add(f"{SITE}/rss.xml", rss([{"title": "Headline", "link": f"{SITE}/y"}]))
    out = discover_feeds(SITE, client_factory=feeds.factory())
    ok = [c for c in out["candidates"] if c["ok"]]
    assert [c["url"] for c in ok] == [f"{SITE}/rss.xml"] and ok[0]["found_by"] == "conventional path"



def test_discover_offers_a_plain_sitemap_when_pages_give_headlines(feeds, monkeypatch):
    monkeypatch.setattr("app.collectors.discover.time.sleep", lambda s: None)
    feeds.add(f"{SITE}/", "<html><head><title>দৈনিক উদাহরণ</title></head></html>", headers={"content-type": "text/html"})
    feeds.add(f"{SITE}/robots.txt", f"User-agent: *\nAllow: /\nSitemap: {SITE}/sitemap.php\nSitemap: {SITE}/tags.xml\n",
              headers={"content-type": "text/plain"})
    feeds.add(f"{SITE}/sitemap.php", _plain_sitemap((f"{SITE}/article/60646", timedelta(minutes=3))), headers=XML)
    feeds.add(f"{SITE}/article/60646", '<html><head><meta property="og:type" content="article">'
              '<meta property="og:title" content="নদীভাঙনে বিলীন শতাধিক ঘর"></head></html>', headers=HTML)
    feeds.add(f"{SITE}/tags.xml", _plain_sitemap((f"{SITE}/topic/flood", timedelta(minutes=1))), headers=XML)
    feeds.add(f"{SITE}/topic/flood", '<html><head><title>বন্যা</title></head></html>', headers=HTML)
    out = discover_feeds(SITE, client_factory=feeds.factory())
    first = out["candidates"][0]
    assert first["url"] == f"{SITE}/sitemap.php" and first["ok"] and first["needs_page_metadata"]
    assert first["samples"] == ["নদীভাঙনে বিলীন শতাধিক ঘর"]
    tags = next(c for c in out["candidates"] if c["url"] == f"{SITE}/tags.xml")
    assert not tags["ok"] and "not stories" in tags["error"]


@pytest.mark.parametrize(
    "title,url,site,expected",
    [
        ("হাসপাতাল থেকে বাসায় ফিরলেন স্বাস্থ্যমন্ত্রী - MZamin", "https://www.mzamin.com/article/1", None,
         "হাসপাতাল থেকে বাসায় ফিরলেন স্বাস্থ্যমন্ত্রী"),
        ("বিএনপির র‍্যালি : Daily Naya Diganta (দৈনিক নয়া দিগন্ত)", "https://dailynayadiganta.com/post/1", None, "বিএনপির র‍্যালি"),
        ("সড়কে প্রাণ গেল দুজনের | Boishakhi Online", "https://boishakhionline.com/national/1", "Boishakhi Online",
         "সড়কে প্রাণ গেল দুজনের"),
        # A colon or a section name that is part of the headline stays.
        ("মোদির প্রশংসা ট্রাম্পের: বললেন ‘খুবই কঠিন মানুষ’", "https://www.mzamin.com/article/2", None,
         "মোদির প্রশংসা ট্রাম্পের: বললেন ‘খুবই কঠিন মানুষ’"),
        ("Enough fertilizer in stock: ministry | National News", "https://www.dainikshiksha.com/en/news/x", "Dainik Shiksha",
         "Enough fertilizer in stock: ministry | National News"),
    ],
)
def test_page_titles_lose_the_site_name(title, url, site, expected):
    from app.collectors.page_meta import clean_page_title

    assert clean_page_title(title, url, site) == expected


def test_discover_marks_a_feed_that_stopped_as_stale(feeds, monkeypatch):
    monkeypatch.setattr("app.collectors.discover.time.sleep", lambda s: None)
    feeds.add(f"{SITE}/", '<html><head><link rel="alternate" type="application/rss+xml" href="/old.xml">'
              '<link rel="alternate" type="application/rss+xml" href="/new.xml"></head></html>', headers=HTML)
    old = (utcnow() - timedelta(days=9)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    new = (utcnow() - timedelta(hours=1)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    feeds.add(f"{SITE}/old.xml", rss([{"title": "পুরোনো খবর", "link": f"{SITE}/o", "pubDate": old}]))
    feeds.add(f"{SITE}/new.xml", rss([{"title": "নতুন খবর", "link": f"{SITE}/n", "pubDate": new}]))
    out = discover_feeds(SITE, client_factory=feeds.factory())
    assert [(c["url"].rsplit("/", 1)[1], c["stale"]) for c in out["candidates"]] == [("new.xml", False), ("old.xml", True)]

# ---- Source packs ---------------------------------------------------------------------------

import json  # noqa: E402

from app.models import Category  # noqa: E402
from app.services.source_import import import_entries, list_packs, load_pack  # noqa: E402


def _write_pack(tmp_path, monkeypatch, sources):
    (tmp_path / "test-pack.json").write_text(json.dumps({
        "newswire_sources": 1,
        "pack": {"name": "Test papers", "description": "Two papers", "verified_at": "2026-11-17"},
        "sources": sources,
    }), encoding="utf-8")
    monkeypatch.setattr("app.services.source_import.PACK_DIR", tmp_path)


PACK_SOURCES = [
    {"name": "পত্রিকা — সর্বশেষ", "publisher": "পত্রিকা", "feed_url": f"{SITE}/feed/", "source_type": "rss",
     "language": "bn", "country_code": "BD", "categories": ["Politics"]},
    {"name": "Paper — News sitemap", "publisher": "Paper", "feed_url": "https://en.example.com/news-sitemap.xml",
     "source_type": "sitemap", "language": "en", "country_code": "BD"},
]


def test_source_pack_listing_and_import_is_idempotent(db, tmp_path, monkeypatch):
    _write_pack(tmp_path, monkeypatch, PACK_SOURCES)
    db.add(Category(name="Politics", slug="politics"))
    db.commit()
    [pack] = list_packs()
    assert pack["id"] == "test-pack" and pack["count"] == 2 and pack["languages"] == ["bn", "en"]
    first = import_entries(db, load_pack("test-pack"))
    db.commit()
    assert first == {"created": 2, "skipped": 0, "errors": []}
    s = db.scalar(select(Source).where(Source.feed_url == f"{SITE}/feed/"))
    assert s.language == "bn" and [c.name for c in s.categories] == ["Politics"] and s.next_fetch_at is not None
    assert import_entries(db, load_pack("test-pack"))["skipped"] == 2
    assert load_pack("../etc/passwd") is None and load_pack("missing") is None


def test_bundled_packs_are_valid():
    """Every bundled pack parses and every entry passes the same validation as an import."""
    from app.core.http import validate_url
    from app.schemas.domain import SourceIn

    for pack in list_packs():
        entries = load_pack(pack["id"])
        urls = [e["feed_url"] for e in entries]
        assert len(urls) == len(set(urls)), f"duplicate feed URLs in {pack['id']}"
        for e in entries:
            body = SourceIn(**{k: v for k, v in e.items() if k in SourceIn.model_fields and k != "category_ids"})
            validate_url(body.feed_url)
            assert body.respect_robots, "bundled sources must respect robots.txt"


# ---- Real-world feed quirks seen on Bangladeshi sites ----------------------------------------

from datetime import timedelta, timezone  # noqa: E402

from app.collectors.parsers import parse_feed  # noqa: E402
from app.core.db import utcnow  # noqa: E402


def _rss(pub: str, title: str = "শিরোনাম", lang: str = "en-US") -> str:
    return (f'<rss version="2.0"><channel><title>t</title><language>{lang}</language><item><title>{title}</title>'
            f"<link>{SITE}/q/{abs(hash(pub))}</link><pubDate>{pub}</pubDate></item></channel></rss>")


@pytest.mark.parametrize(
    "pub,expected",
    [
        ("Tue, 17 Nov 2026, 11:54:23 +0600", "2026-11-17T05:54:23+00:00"),  # extra comma (BSS)
        ("Tuesday, 17 November 2026, 11:54:23 +0600", "2026-11-17T05:54:23+00:00"),  # long names
    ],
)
def test_lenient_rss_dates(pub, expected):
    entry = parse_feed(_rss(pub).encode(), content_type="application/rss+xml", source_type="rss").entries[0]
    assert entry.published_at.isoformat() == expected


def test_sitemap_date_without_time_is_not_a_publication_time():
    xml = news_sitemap([(f"{SITE}/d1", "শিরোনাম", "2026-11-17")])
    assert parse_sitemap(xml.encode()).entries[0].published_at is None


def _future_item(db, feeds, hours: float, country: str | None, path: str):
    future = (utcnow() + timedelta(hours=hours)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    feeds.add(f"{SITE}/{path}", _rss(future, "চট্টগ্রামে বন্দরের কার্যক্রম স্বাভাবিক"))
    source = make_source(db, f"{SITE}/{path}", source_type="rss")
    source.country_code = country
    db.commit()
    collect_source(db, source, client_factory=feeds.factory())
    return db.scalar(select(NewsItem).where(NewsItem.source_id == source.id))


def test_future_dates_are_rejected_and_language_follows_the_script(db, feeds):
    item = _future_item(db, feeds, 6, None, "wp-feed")
    assert item.language == "bn", "a Bangla headline is Bangla whatever the feed declares"
    assert item.published_at is None and "rejected_published_at" in item.extra
    assert item.sort_at == item.retrieved_at


def test_dhaka_time_labelled_as_utc_is_corrected(db, feeds):
    # Published 30 minutes ago in Dhaka (UTC+6), but labelled +0000: 5.5 hours "in the future".
    item = _future_item(db, feeds, 5.5, "BD", "bd-feed")
    assert "published_at_as_given" in item.extra and "rejected_published_at" not in item.extra
    age = item.retrieved_at - item.published_at.replace(tzinfo=item.retrieved_at.tzinfo or timezone.utc)
    assert timedelta(minutes=29) < age < timedelta(minutes=31)
    # More than the zone's offset ahead cannot be a local-time mix-up: rejected.
    assert _future_item(db, feeds, 8, "BD", "bd-feed-2").published_at is None


def test_plain_sitemap_skips_section_pages_and_old_entries(db, feeds):
    feeds.add(f"{SITE}/sm.xml", _plain_sitemap(
        (f"{SITE}/national", timedelta(minutes=5)),           # section page
        (f"{SITE}/national/2026111701", timedelta(minutes=10)),  # story (number in the address)
        (f"{SITE}/old/2025010101", timedelta(days=5)),          # too old to look up
    ), headers=XML)
    feeds.add(f"{SITE}/national", '<html><head><meta property="og:type" content="website">'
              '<title>জাতীয় সংবাদ</title></head></html>', headers=HTML)
    feeds.add(f"{SITE}/national/2026111701", '<html><head><meta property="og:type" content="website">'
              '<meta property="og:title" content="সংসদে বাজেট পাস"></head></html>', headers=HTML)
    source = make_source(db, f"{SITE}/sm.xml", extract_page_metadata=True)
    first = collect_source(db, source, client_factory=feeds.factory()).log
    assert (first.items_new, first.items_skipped) == (1, 2)
    assert [r for r in feeds.requests if str(r.url).endswith("/old/2025010101")] == []
    # The section page is remembered and not fetched again on the next run.
    before = len([r for r in feeds.requests if str(r.url).endswith("/national")])
    collect_source(db, source, client_factory=feeds.factory())
    assert len([r for r in feeds.requests if str(r.url).endswith("/national")]) == before == 1


def test_english_text_declared_as_bangla_uses_the_source_language(db, feeds):
    feeds.add(f"{SITE}/en-feed", _rss("Tue, 17 Nov 2026 03:00:00 GMT", "Cabinet approves Padma rail link", lang="bn"))
    source = make_source(db, f"{SITE}/en-feed", source_type="rss", language="en")
    collect_source(db, source, client_factory=feeds.factory())
    assert db.scalar(select(NewsItem.language).where(NewsItem.source_id == source.id)) == "en"


def test_bare_sitemap_date_is_kept_as_a_day_not_a_time():
    parsed = parse_sitemap(news_sitemap([(f"{SITE}/national/b1", "পুরোনো খবর", "2026-11-16")]).encode())
    e = parsed.entries[0]
    assert e.published_at is None
    assert e.published_day is not None and e.published_day.isoformat() == "2026-11-16"
    # A full timestamp wins; no day is recorded alongside it.
    assert parse_sitemap(news_sitemap(ITEMS).encode()).entries[0].published_day is None


def test_undated_backlog_does_not_sort_above_todays_news(db, feeds):
    """Sitemaps with bare dates (Dainik Bangla, New Age) used to sort a backlog of older stories as
    "just retrieved", pushing every other paper off the top of Live News."""
    from datetime import timedelta

    from app.core.db import utcnow

    old_day = (utcnow() - timedelta(days=3)).date().isoformat()
    feeds.add(f"{SITE}/news-sitemap.xml",
              news_sitemap([(f"{SITE}/national/old", "তিন দিন আগের খবর", old_day)]), headers=XML)
    source = make_source(db, f"{SITE}/news-sitemap.xml")
    collect_source(db, source, client_factory=feeds.factory())
    item = db.scalar(select(NewsItem).where(NewsItem.url == f"{SITE}/national/old"))
    assert item.published_at is None
    assert item.extra["published_day"] == old_day
    sort_at = item.sort_at if item.sort_at.tzinfo else item.sort_at.replace(tzinfo=utcnow().tzinfo)
    # Sorted at the end of that day in Dhaka, not at the moment it was collected.
    assert sort_at <= utcnow() - timedelta(days=1)
