"""Find the feeds a news website offers, starting from its home page.

Looks only where publishers advertise feeds: <link rel="alternate"> tags on the home page and
news/feed sitemaps listed in robots.txt. If neither yields anything it tries a few conventional
paths (/feed, /rss, /rss.xml, /sitemap-news.xml ...). If still nothing works, the other sitemaps
in robots.txt are tried: a plain sitemap (addresses only, no headlines) can still serve as a
source when page metadata lookup reads each new story's headline, so two of its newest pages
are looked at to show what that would give. Every candidate is fetched once and parsed, so the
result says which ones actually work. Respects robots.txt; stores nothing.
"""

from __future__ import annotations

import time
import uuid
from datetime import timedelta
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from app.collectors import robots
from app.collectors.ingest import ClientFactory, SourceSnapshot, fetch_http
from app.collectors.page_meta import clean_page_title, fetch_page_metadata, looks_like_article
from app.collectors.parsers import FeedParseError, parse_feed
from app.core.config import get_settings
from app.core.db import utcnow
from app.core.http import ResponseTooLarge, UnsafeURLError, describe_error, describe_http_status, make_client, safe_request

FEED_TYPES = {
    "application/rss+xml": "rss",
    "application/atom+xml": "atom",
    "application/feed+json": "json",
    "application/json": "json",
}
CONVENTIONAL_PATHS = ["/feed", "/rss", "/rss.xml", "/feed.xml", "/sitemap-news.xml", "/news-sitemap.xml", "/sitemap_news.xml"]
MAX_CANDIDATES = 10
MAX_PLAIN_SITEMAPS = 3  # other robots.txt sitemaps tried when nothing else works
PAGE_SAMPLES = 2
STALE_AFTER = timedelta(days=3)  # a news feed whose newest entry is older than this has stopped


def _same_site(a: str, b: str) -> bool:
    ha, hb = urlsplit(a).hostname or "", urlsplit(b).hostname or ""
    strip = lambda h: h[4:] if h.startswith("www.") else h  # noqa: E731
    return strip(ha) == strip(hb) or strip(ha).endswith("." + strip(hb)) or strip(hb).endswith("." + strip(ha))


def _homepage_links(client: httpx.Client, url: str, respect_robots: bool) -> tuple[list[dict], str | None, str | None]:
    """(<link rel=alternate> candidates, page title, error)."""
    if respect_robots:
        allowed, reason = robots.is_allowed(client, url)
        if not allowed:
            return [], None, reason
    try:
        resp = safe_request(client, "GET", url, max_bytes=3_000_000, headers={"Accept": "text/html"})
    except (UnsafeURLError, ResponseTooLarge, httpx.HTTPError) as exc:
        return [], None, describe_error(exc)
    if resp.status_code != 200:
        return [], None, describe_http_status(resp)
    soup = BeautifulSoup(resp.content, "html.parser")
    title = soup.title.string.strip() if soup.title and soup.title.string else None
    found = []
    for link in soup.find_all("link", href=True):
        rels = [r.lower() for r in (link.get("rel") or [])]
        kind = FEED_TYPES.get((link.get("type") or "").lower().split(";")[0].strip())
        if "alternate" in rels and kind:
            found.append({"url": urljoin(str(resp.url), link["href"].strip()), "source_type": kind,
                          "title": (link.get("title") or "").strip() or None, "found_by": "home page link"})
    return found, title, None


def _is_newsy(url: str) -> bool:
    low = url.lower()
    return "news" in low or "rss" in low or "feed" in low


def _check(client: httpx.Client, c: dict, home: str, respect_robots: bool, delay: float) -> dict:
    outcome = fetch_http(client, SourceSnapshot(uuid.uuid4(), c["url"], None, None, respect_robots, c["source_type"]))
    row = {**c, "ok": False, "entries": 0, "feed_format": None, "language": None,
           "latest": None, "error": outcome.error, "note": outcome.note,
           "same_site": _same_site(c["url"], home), "needs_page_metadata": False, "samples": [], "stale": False}
    if outcome.status != "success":
        return row
    try:
        parsed = parse_feed(outcome.content or b"", content_type=outcome.content_type, source_type=c["source_type"])
    except FeedParseError as exc:
        row["error"] = str(exc)
        return row
    dates = [e.published_at for e in parsed.entries if e.published_at]
    titled = [e for e in parsed.entries if e.title]
    row.update(
        ok=bool(titled), entries=len(parsed.entries), feed_format=parsed.feed_format,
        language=parsed.language, title=c["title"] or parsed.title,
        latest=max(dates).isoformat() if dates else None,
        stale=bool(dates) and max(dates) < utcnow() - STALE_AFTER,
        source_type="sitemap" if parsed.feed_format.endswith("sitemap") else c["source_type"],
        error=None if titled else (parsed.warning or "No entries with headlines"),
        samples=[e.title for e in titled[:3]],
    )
    if not titled and parsed.feed_format == "sitemap" and parsed.entries:
        # Plain sitemap: would page metadata lookup give headlines? Look at the newest pages.
        samples = []
        for e in parsed.entries[:PAGE_SAMPLES]:
            time.sleep(delay)
            meta = fetch_page_metadata(client, e.url, respect_robots)
            if meta is not None and meta.title and looks_like_article(e.url, meta):
                samples.append(clean_page_title(meta.title, e.url, meta.site_name))
        if samples:
            row.update(ok=True, needs_page_metadata=True, samples=samples, error=None,
                       note="Plain sitemap: headlines are read from each new story's page (page metadata lookup)")
        else:
            row["error"] = "Plain sitemap whose newest pages are not stories (section or tag pages)"
    return row


def discover_feeds(url: str, *, respect_robots: bool = True, client_factory: ClientFactory | None = None) -> dict:
    parts = urlsplit(url)
    home = f"{parts.scheme}://{parts.netloc}/"
    factory = client_factory or make_client
    delay = get_settings().per_host_delay_seconds
    with factory() as client:
        candidates, site_title, home_error = _homepage_links(client, url if parts.path not in ("", "/") else home, respect_robots)
        listed = robots.sitemaps(client, home)
        for sm in listed:
            if _is_newsy(sm):
                candidates.append({"url": sm, "source_type": "sitemap", "title": None, "found_by": "robots.txt"})
        if not candidates:
            candidates = [
                {"url": urljoin(home, path), "source_type": "sitemap" if "sitemap" in path else "rss",
                 "title": None, "found_by": "conventional path"}
                for path in CONVENTIONAL_PATHS
            ]
        seen: set[str] = set()

        def unique(rows: list[dict]) -> list[dict]:
            out = []
            for c in rows:
                if c["url"] not in seen and c["url"].startswith(("http://", "https://")):
                    seen.add(c["url"])
                    out.append(c)
            return out

        results = []
        for i, c in enumerate(unique(candidates)[:MAX_CANDIDATES]):
            if i:
                time.sleep(delay)
            results.append(_check(client, c, home, respect_robots, delay))
        if not any(r["ok"] for r in results):
            others = [{"url": sm, "source_type": "sitemap", "title": None, "found_by": "robots.txt (plain sitemap)"}
                      for sm in listed if not _is_newsy(sm)]
            for c in unique(others)[:MAX_PLAIN_SITEMAPS]:
                time.sleep(delay)
                results.append(_check(client, c, home, respect_robots, delay))
    # Working feeds first (feeds with headlines before plain sitemaps), newest content first.
    working = [r for r in results if r["ok"]]
    working.sort(key=lambda r: r["latest"] or "", reverse=True)
    working.sort(key=lambda r: (r["stale"], r["needs_page_metadata"]))
    return {
        "site": home,
        "site_title": site_title,
        "home_page_error": home_error,
        "candidates": working + [r for r in results if not r["ok"]],
    }
