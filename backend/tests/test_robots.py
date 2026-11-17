"""robots.txt: RFC 9309 matching, and telling a publisher's "no" apart from a connection problem."""

import httpx

from app.collectors import robots
from app.collectors.ingest import collect_source
from app.core.http import non_public_hint
from app.models import Source
from tests.conftest import rss

SITE = "https://news.example.com"
FEED = f"{SITE}/rss"


def allows(text: str, path: str, agent: str = "NEWSWIRE") -> bool:
    return robots.parse(text).allows(agent, SITE + path)


def test_longest_match_wins_over_order():
    text = "User-agent: *\nDisallow: /\nAllow: /feed\n"
    assert allows(text, "/feed/")
    assert not allows(text, "/politics/1")


def test_allow_wins_a_tie():
    assert allows("User-agent: *\nDisallow: /rss\nAllow: /rss\n", "/rss")


def test_wildcards_and_end_anchor():
    text = "User-agent: *\nDisallow: /*?page=\nDisallow: /*.php$\n"
    assert not allows(text, "/news?page=2")
    assert not allows(text, "/sitemap.php")
    assert allows(text, "/sitemap.php?x=1")
    assert allows(text, "/news")


def test_specific_group_replaces_star_and_groups_merge():
    text = (
        "User-agent: *\nDisallow: /\n\n"
        "User-agent: newswire\nDisallow: /private\n\n"
        "User-agent: NEWSWIRE\nDisallow: /drafts\n"
    )
    assert allows(text, "/rss")
    assert not allows(text, "/private/x")
    assert not allows(text, "/drafts/y")


def test_star_groups_merge():
    # Live Narayanganj's file lists `User-agent: *` twice; RFC 9309 merges them.
    text = "User-agent: *\nDisallow: /cmedianet\nUser-agent: Googlebot\nDisallow:\nUser-agent: *\nDisallow: /\n"
    assert not allows(text, "/feed")


def test_agent_is_not_matched_by_substring():
    # urllib.robotparser treated "news" as matching "newswire".
    assert allows("User-agent: news\nDisallow: /\n", "/rss")


def test_empty_disallow_allows_and_bom_is_ignored():
    assert allows("﻿User-agent: *\nDisallow:\n", "/anything")


def test_bangla_paths_match_encoded_or_not():
    text = "User-agent: *\nDisallow: /%E0%A6%96\n"
    assert not allows(text, "/খবর/1")


def test_sitemaps_are_collected():
    rules = robots.parse("Sitemap: https://news.example.com/news.xml\nUser-agent: *\nDisallow:\n")
    assert rules.sitemaps == ["https://news.example.com/news.xml"]


def make_source(db) -> Source:
    s = Source(name="Example", publisher="Example", source_type="rss", feed_url=FEED, website_url=SITE,
               respect_robots=True, is_active=True)
    db.add(s)
    db.commit()
    return s


def test_unreachable_robots_is_an_error_with_the_real_cause_not_blocked(db, feeds):
    def boom(request):
        raise httpx.ConnectTimeout("handshake timed out", request=request)

    feeds.routes[f"{SITE}/robots.txt"] = boom
    feeds.add(FEED, rss([{"title": "x", "link": f"{SITE}/x"}]))
    res = collect_source(db, make_source(db), client_factory=feeds.factory())
    assert res.log.status == "error"
    assert "Connection timed out" in res.log.error_message
    assert "not the publisher blocking" in res.log.error_message
    assert all(str(r.url) != FEED for r in feeds.requests)  # still no fetch without robots.txt


def test_one_transient_failure_is_retried(db, feeds):
    calls = {"n": 0}

    def flaky(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectTimeout("handshake timed out", request=request)
        return httpx.Response(200, text="User-agent: *\nDisallow:\n")

    feeds.routes[f"{SITE}/robots.txt"] = flaky
    feeds.add(FEED, rss([{"title": "Retried story", "link": f"{SITE}/r"}]))
    res = collect_source(db, make_source(db), client_factory=feeds.factory())
    assert res.log.status == "success" and calls["n"] == 2


def test_cached_copy_is_used_while_robots_is_unreachable(feeds, monkeypatch):
    feeds.add(f"{SITE}/robots.txt", "User-agent: *\nDisallow: /private\n", headers={"content-type": "text/plain"})
    client = feeds.factory()()
    assert robots.check(client, FEED).allowed

    def down(request):
        return httpx.Response(503, text="maintenance")

    feeds.routes[f"{SITE}/robots.txt"] = down
    # Expire the fresh copy; the stale one is still usable.
    entry = robots._cache[SITE]
    entry.expires = 0
    assert robots.check(client, FEED).allowed
    assert not robots.check(client, f"{SITE}/private/1").allowed
    assert robots.check(client, f"{SITE}/private/1").kind == "disallowed"


def test_disallow_is_still_blocked(db, feeds):
    feeds.add(f"{SITE}/robots.txt", "User-agent: *\nDisallow: /rss\n", headers={"content-type": "text/plain"})
    feeds.add(FEED, rss([{"title": "x", "link": f"{SITE}/x"}]))
    res = collect_source(db, make_source(db), client_factory=feeds.factory())
    assert res.log.status == "blocked" and res.log.error_message == "Disallowed by the publisher's robots.txt"


def test_bot_check_is_named(db, feeds):
    feeds.add(FEED, "<html><title>Just a moment...</title></html>", status=403,
              headers={"content-type": "text/html", "server": "cloudflare", "cf-mitigated": "challenge"})
    res = collect_source(db, make_source(db), client_factory=feeds.factory())
    assert res.log.status == "error"
    assert "bot protection (cloudflare)" in res.log.error_message
    assert "does not get around" in res.log.error_message


def test_non_public_hints():
    assert "fake-IP" in non_public_hint("198.18.0.7")
    assert "this machine" in non_public_hint("127.0.0.1")
    assert "local DNS" in non_public_hint("10.1.2.3")
