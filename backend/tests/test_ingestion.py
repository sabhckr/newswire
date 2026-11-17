"""Integration tests: RSS/Atom/JSON ingestion, conditional requests, dedup, errors, backoff."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.collectors.ingest import collect_source, probe_source_url, run_collection_cycle
from app.core.db import utcnow
from app.models import NewsGeography, NewsItem, NewsItemSource, Source, SourceFetchLog
from tests.conftest import rss

FEED = "https://news.example.com/rss"


def make_source(db, url=FEED, **kw) -> Source:
    s = Source(
        name=kw.pop("name", "Example News"),
        publisher=kw.pop("publisher", "Example News"),
        feed_url=url,
        source_type=kw.pop("source_type", "rss"),
        polling_interval_seconds=kw.pop("polling_interval_seconds", 300),
        **kw,
    )
    db.add(s)
    db.commit()
    return s


def test_rss_ingestion_persists_items_and_metadata(db, feeds):
    feeds.add(FEED, rss([
        {"title": "Bangladesh approves new railway line", "link": "https://news.example.com/a?utm_source=rss",
         "description": "<p>DHAKA, Nov 17 (Reuters) - The cabinet approved a <b>railway</b> project.</p>",
         "pubDate": "Mon, 16 Nov 2026 10:00:00 GMT", "category": "Bangladesh", "image": "https://img.example.com/a.jpg"},
        {"title": "Markets close higher", "link": "https://news.example.com/b"},
    ]), headers={"content-type": "application/rss+xml", "etag": '"v1"', "last-modified": "Mon, 16 Nov 2026 10:00:00 GMT"})
    source = make_source(db, country_code="BD", language="en")
    result = collect_source(db, source, client_factory=feeds.factory())

    assert result.log.status == "success"
    assert result.log.items_found == 2 and result.log.items_new == 2
    item = db.scalar(select(NewsItem).where(NewsItem.title.like("Bangladesh%")))
    assert item.normalized_url == "https://news.example.com/a"
    assert item.summary.startswith("DHAKA, Nov 17 (Reuters) - The cabinet approved a railway project")
    assert item.published_at is not None and item.published_at.year == 2026
    assert item.image_urls == ["https://img.example.com/a.jpg"]
    assert item.language == "en"
    other = db.scalar(select(NewsItem).where(NewsItem.title == "Markets close higher"))
    assert other.published_at is None, "missing dates must stay missing"
    assert other.sort_at == other.retrieved_at

    db.refresh(source)
    assert source.etag == '"v1"' and source.consecutive_failures == 0
    assert source.last_success_at is not None and source.next_fetch_at > utcnow()

    roles = {(g.role, g.method) for g in db.scalars(select(NewsGeography).where(NewsGeography.news_item_id == item.id))}
    assert ("publisher", "source_metadata") in roles
    assert ("event", "dateline") in roles
    assert ("mentioned", "text_match") in roles
    assert ("article", "feed_category") in roles


def test_conditional_request_and_not_modified(db, feeds):
    calls = []

    def handler(request):
        import httpx

        calls.append(dict(request.headers))
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, headers={"etag": '"v1"', "content-type": "application/rss+xml"},
                              content=rss([{"title": "Only story", "link": "https://news.example.com/1"}]).encode())

    feeds.routes[FEED] = handler
    source = make_source(db)
    collect_source(db, source, client_factory=feeds.factory())
    second = collect_source(db, source, client_factory=feeds.factory())
    assert second.log.status == "not_modified"
    assert calls[-1].get("if-none-match") == '"v1"'
    assert db.scalar(select(func.count()).select_from(NewsItem)) == 1


def test_duplicate_url_across_sources_creates_one_record(db, feeds):
    body = rss([{"title": "Port expansion begins in Chattogram", "link": "https://wire.example.org/story-1?utm_medium=x"}])
    feeds.add(FEED, body)
    feeds.add("https://other.example.net/feed", rss([{"title": "Port expansion begins in Chattogram", "link": "https://wire.example.org/story-1/"}]))
    s1 = make_source(db)
    s2 = make_source(db, url="https://other.example.net/feed", name="Other", publisher="Other")
    collect_source(db, s1, client_factory=feeds.factory())
    res = collect_source(db, s2, client_factory=feeds.factory())
    assert res.log.items_new == 0 and res.log.items_duplicate == 1
    assert db.scalar(select(func.count()).select_from(NewsItem)) == 1
    assert db.scalar(select(func.count()).select_from(NewsItemSource)) == 2


def test_content_hash_and_headline_duplicates_are_flagged(db, feeds):
    feeds.add(FEED, rss([
        {"title": "Government unveils national budget for next fiscal year", "link": "https://a.example.com/1", "description": "Same wire copy."},
    ]))
    feeds.add("https://b.example.com/rss", rss([
        {"title": "Government unveils national budget for next fiscal year", "link": "https://b.example.com/x", "description": "Same wire copy."},
        {"title": "Government unveils national budget for the next fiscal year", "link": "https://b.example.com/y", "description": "Different text."},
    ]))
    collect_source(db, make_source(db), client_factory=feeds.factory())
    collect_source(db, make_source(db, url="https://b.example.com/rss", name="B", publisher="B"), client_factory=feeds.factory())
    original = db.scalar(select(NewsItem).where(NewsItem.url == "https://a.example.com/1"))
    copy = db.scalar(select(NewsItem).where(NewsItem.url == "https://b.example.com/x"))
    near = db.scalar(select(NewsItem).where(NewsItem.url == "https://b.example.com/y"))
    assert not original.is_duplicate
    assert copy.is_duplicate and copy.duplicate_reason == "content_hash" and copy.duplicate_of_id == original.id
    assert near.is_duplicate and near.duplicate_reason == "headline_similarity" and near.duplicate_of_id == original.id


def test_atom_and_json_feed_parsing(db, feeds):
    atom = """<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"><title>Atom</title>
    <entry><title>Atom story about floods</title><link href="https://atom.example.com/1"/><id>tag:1</id>
    <updated>2026-11-16T08:00:00Z</updated><summary>Floods in Sylhet.</summary></entry></feed>"""
    feeds.add("https://atom.example.com/feed", atom, headers={"content-type": "application/atom+xml"})
    jf = {"version": "https://jsonfeed.org/version/1.1", "title": "JF", "items": [
        {"id": "1", "url": "https://jf.example.com/1", "title": "JSON feed item", "date_published": "2026-11-16T09:00:00Z", "summary": "Hello"}]}
    import json

    feeds.add("https://jf.example.com/feed.json", json.dumps(jf), headers={"content-type": "application/feed+json"})
    a = collect_source(db, make_source(db, url="https://atom.example.com/feed", source_type="atom"), client_factory=feeds.factory())
    j = collect_source(db, make_source(db, url="https://jf.example.com/feed.json", source_type="json"), client_factory=feeds.factory())
    assert a.log.items_new == 1 and j.log.items_new == 1
    assert db.scalar(select(NewsItem).where(NewsItem.url == "https://jf.example.com/1")).published_at.hour == 9


def test_json_api_with_field_mapping(db, feeds):
    import json

    payload = {"data": {"articles": [{"headline": "API story", "link": "https://api.example.com/s/1", "ts": 1794830400, "lede": "Text"}]}}
    feeds.add("https://api.example.com/v1/news", json.dumps(payload), headers={"content-type": "application/json"})
    cfg = {"items_path": "data.articles", "fields": {"title": "headline", "url": "link", "published_at": "ts", "summary": "lede"}}
    res = collect_source(db, make_source(db, url="https://api.example.com/v1/news", source_type="json", api_config=cfg), client_factory=feeds.factory())
    assert res.log.items_new == 1
    item = db.scalar(select(NewsItem))
    assert item.title == "API story" and item.summary == "Text" and item.published_at is not None


def test_http_error_sets_backoff_and_does_not_raise(db, feeds):
    feeds.add(FEED, "oops", status=503, headers={"content-type": "text/plain"})
    source = make_source(db)
    before = utcnow()
    collect_source(db, source, client_factory=feeds.factory())
    collect_source(db, source, client_factory=feeds.factory())
    db.refresh(source)
    assert source.consecutive_failures == 2
    assert source.last_error == "HTTP 503"
    assert source.next_fetch_at >= before + timedelta(seconds=500)  # 300s * 2^(2-1) with jitter
    assert db.scalar(select(func.count()).select_from(SourceFetchLog).where(SourceFetchLog.status == "error")) == 2


def test_html_page_is_reported_as_not_a_feed(db, feeds):
    feeds.add(FEED, "<!doctype html><html><body>Hi</body></html>", headers={"content-type": "text/html"})
    res = collect_source(db, make_source(db), client_factory=feeds.factory())
    assert res.log.status == "error" and "HTML page" in res.log.error_message


def test_robots_disallow_blocks_fetch(db, feeds):
    feeds.add("https://news.example.com/robots.txt", "User-agent: *\nDisallow: /rss\n", headers={"content-type": "text/plain"})
    feeds.add(FEED, rss([{"title": "x", "link": "https://news.example.com/x"}]))
    res = collect_source(db, make_source(db), client_factory=feeds.factory())
    assert res.log.status == "blocked"
    assert all(str(r.url) != FEED for r in feeds.requests)


def test_scheduler_cycle_isolates_failures(db, feeds):
    feeds.add(FEED, rss([{"title": "Good source story", "link": "https://news.example.com/g"}]))
    feeds.add("https://broken.example.com/rss", "nope", status=500, headers={"content-type": "text/plain"})
    make_source(db)
    make_source(db, url="https://broken.example.com/rss", name="Broken", publisher="Broken")
    stats = run_collection_cycle(db, client_factory=feeds.factory())
    assert stats["sources"] == 2 and stats["new_items"] == 1 and stats["errors"] == 1
    # Nothing is due right after a cycle.
    assert run_collection_cycle(db, client_factory=feeds.factory())["sources"] == 0


def test_probe_reports_sample_without_storing(db, feeds):
    feeds.add(FEED, rss([{"title": "Probe story", "link": "https://news.example.com/p"}], title="Probe Feed"))
    out = probe_source_url(FEED, client_factory=feeds.factory())
    assert out["ok"] and out["feed_title"] == "Probe Feed" and out["entries"] == 1
    assert db.scalar(select(func.count()).select_from(NewsItem)) == 0
