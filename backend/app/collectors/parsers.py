"""Parse RSS, Atom, JSON Feed, news sitemaps and permitted JSON APIs into a common structure.

Only what the feed itself supplies is kept. HTML is reduced to plain text. Nothing is
invented: a missing date stays missing, a missing image stays missing.
"""

from __future__ import annotations

import calendar
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import xml.etree.ElementTree as ET

import feedparser
from bs4 import BeautifulSoup

from app.core.text import truncate

SUMMARY_LIMIT = 5000
CONTENT_LIMIT = 50000


class FeedParseError(Exception):
    pass


@dataclass
class ParsedEntry:
    title: str | None
    url: str | None
    guid: str | None = None
    published_at: datetime | None = None
    # The publication day when the feed gives a date without a time of day (some news sitemaps).
    # It is not a publication time, but it bounds one: the story is no newer than that day.
    published_day: date | None = None
    summary: str | None = None
    content: str | None = None
    author: str | None = None
    language: str | None = None
    categories: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    location_text: str | None = None
    geo_point: tuple[float, float] | None = None
    canonical_url: str | None = None


@dataclass
class ParsedFeed:
    title: str | None
    link: str | None
    language: str | None
    feed_format: str
    entries: list[ParsedEntry]
    warning: str | None = None
    children: list[tuple[str, datetime | None]] = field(default_factory=list)  # sitemap index


_WS = re.compile(r"\s+")


def html_to_text(value: str | None) -> str | None:
    if not value:
        return None
    if "<" not in value and "&" not in value:
        text = value
    else:
        soup = BeautifulSoup(value, "html.parser")
        for tag in soup(["script", "style", "iframe", "noscript"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
    text = _WS.sub(" ", text).strip()
    return text or None


def _struct_to_dt(st) -> datetime | None:
    if not st:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)
    except (OverflowError, ValueError, TypeError):
        return None


def parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        ts = value / 1000 if value > 10**11 else value
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, ValueError):
            return None
    s = str(value).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(s)
        except (TypeError, ValueError, IndexError):
            return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _norm_lang(lang: str | None) -> str | None:
    if not lang:
        return None
    lang = str(lang).strip().replace("_", "-").lower()
    return lang[:16] or None


def _images_from_entry(e: Any) -> list[str]:
    urls: list[str] = []

    def add(u: str | None):
        if u and isinstance(u, str) and u.startswith(("http://", "https://")) and u not in urls:
            urls.append(u)

    for m in e.get("media_content", []) or []:
        medium = (m.get("medium") or "").lower()
        mtype = (m.get("type") or "").lower()
        if medium == "image" or mtype.startswith("image/") or (not medium and not mtype):
            add(m.get("url"))
    for m in e.get("media_thumbnail", []) or []:
        add(m.get("url"))
    for enc in e.get("enclosures", []) or []:
        if (enc.get("type") or "").lower().startswith("image/"):
            add(enc.get("href") or enc.get("url"))
    for link in e.get("links", []) or []:
        if link.get("rel") == "enclosure" and (link.get("type") or "").lower().startswith("image/"):
            add(link.get("href"))
    return urls[:10]


def _parse_xml(content: bytes, content_type: str | None) -> ParsedFeed:
    parsed = feedparser.parse(content, response_headers={"content-type": content_type or "application/xml"})
    entries_raw = parsed.get("entries") or []
    version = parsed.get("version") or ""
    if not entries_raw:
        head = content[:600].lower()
        if b"<html" in head or b"<!doctype html" in head:
            raise FeedParseError("The URL returned an HTML page, not a feed")
        if parsed.get("bozo") and not version:
            raise FeedParseError(f"Malformed feed: {parsed.get('bozo_exception')}"[:300])
    feed = parsed.get("feed", {})
    feed_lang = _norm_lang(feed.get("language"))
    entries: list[ParsedEntry] = []
    for e in entries_raw:
        # feedparser rejects some real-world formats ("Tue, 17 Nov 2026, 11:54:23 +0600",
        # "Tuesday, 17 November 2026, …"); the lenient parser below reads those.
        published = (
            _struct_to_dt(e.get("published_parsed"))
            # Read "updated" only when the entry has it: feedparser warns on the implicit alias.
            or (_struct_to_dt(dict.get(e, "updated_parsed")) or parse_datetime(dict.get(e, "updated")))
            or parse_datetime(dict.get(e, "published"))
        )
        content_text = None
        if e.get("content"):
            content_text = html_to_text(e["content"][0].get("value"))
        summary_text = html_to_text(e.get("summary"))
        if summary_text and content_text and summary_text == content_text[: len(summary_text)]:
            pass
        categories = [t.get("term") for t in (e.get("tags") or []) if t.get("term")]
        geo_point = None
        point = e.get("georss_point") or (e.get("where") or {}).get("coordinates")
        if isinstance(point, str):
            parts = point.split()
            if len(parts) == 2:
                try:
                    geo_point = (float(parts[0]), float(parts[1]))
                except ValueError:
                    geo_point = None
        canonical = None
        for link in e.get("links", []) or []:
            if link.get("rel") == "canonical":
                canonical = link.get("href")
        entries.append(
            ParsedEntry(
                title=html_to_text(e.get("title")),
                url=(e.get("link") or "").strip() or None,
                guid=(e.get("id") or None),
                published_at=published,
                summary=truncate(summary_text, SUMMARY_LIMIT),
                content=truncate(content_text, CONTENT_LIMIT),
                author=truncate(html_to_text(e.get("author")), 300),
                language=_norm_lang(e.get("language")) or feed_lang,
                categories=[c[:200] for c in categories][:30],
                image_urls=_images_from_entry(e),
                location_text=truncate(e.get("georss_featurename") or e.get("dc_coverage"), 300),
                geo_point=geo_point,
                canonical_url=canonical,
            )
        )
    warning = None
    if parsed.get("bozo") and entries:
        warning = f"Feed parsed with warnings: {parsed.get('bozo_exception')}"[:300]
    fmt = "atom" if version.startswith("atom") else ("rss" if version else "unknown")
    return ParsedFeed(feed.get("title"), feed.get("link"), feed_lang, fmt, entries, warning)


def _get_path(obj: Any, path: str | None) -> Any:
    if not path:
        return None
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("name") or value.get("title") or value.get("text")
    if isinstance(value, list):
        value = ", ".join(str(_as_text(v)) for v in value if v)
    return html_to_text(str(value)) if value else None


DEFAULT_API_FIELDS = {
    "title": "title",
    "url": "url",
    "guid": "id",
    "published_at": "published_at",
    "summary": "description",
    "content": "content",
    "author": "author",
    "image": "image",
    "language": "language",
    "category": "category",
    "location": "location",
}


def _parse_json(content: bytes, api_config: dict | None) -> ParsedFeed:
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise FeedParseError(f"Invalid JSON: {exc}") from exc

    # JSON Feed (https://jsonfeed.org)
    if isinstance(data, dict) and str(data.get("version", "")).startswith("https://jsonfeed.org"):
        lang = _norm_lang(data.get("language"))
        entries = []
        for it in data.get("items", []) or []:
            authors = it.get("authors") or ([it["author"]] if it.get("author") else [])
            images = [u for u in (it.get("image"), it.get("banner_image")) if u]
            entries.append(
                ParsedEntry(
                    title=html_to_text(it.get("title")),
                    url=it.get("url") or it.get("external_url"),
                    guid=str(it.get("id")) if it.get("id") is not None else None,
                    published_at=parse_datetime(it.get("date_published") or it.get("date_modified")),
                    summary=truncate(html_to_text(it.get("summary")), SUMMARY_LIMIT),
                    content=truncate(html_to_text(it.get("content_text") or it.get("content_html")), CONTENT_LIMIT),
                    author=truncate(", ".join(a.get("name", "") for a in authors if isinstance(a, dict)) or None, 300),
                    language=_norm_lang(it.get("language")) or lang,
                    categories=[str(t)[:200] for t in (it.get("tags") or [])][:30],
                    image_urls=images,
                )
            )
        return ParsedFeed(data.get("title"), data.get("home_page_url"), lang, "jsonfeed", entries)

    cfg = api_config or {}
    items = _get_path(data, cfg.get("items_path")) if cfg.get("items_path") else data
    if isinstance(items, dict):
        for key in ("items", "articles", "results", "data", "entries"):
            if isinstance(items.get(key), list):
                items = items[key]
                break
    if not isinstance(items, list):
        raise FeedParseError("Could not find a list of items in the JSON response; set items_path")
    fields = {**DEFAULT_API_FIELDS, **(cfg.get("fields") or {})}
    entries = []
    for it in items:
        if not isinstance(it, dict):
            continue
        image = _get_path(it, fields["image"])
        cats = _get_path(it, fields["category"])
        cat_list = [str(_as_text(c)) for c in cats] if isinstance(cats, list) else ([_as_text(cats)] if cats else [])
        entries.append(
            ParsedEntry(
                title=_as_text(_get_path(it, fields["title"])),
                url=_get_path(it, fields["url"]),
                guid=str(_get_path(it, fields["guid"])) if _get_path(it, fields["guid"]) is not None else None,
                published_at=parse_datetime(_get_path(it, fields["published_at"])),
                summary=truncate(_as_text(_get_path(it, fields["summary"])), SUMMARY_LIMIT),
                content=truncate(_as_text(_get_path(it, fields["content"])), CONTENT_LIMIT),
                author=truncate(_as_text(_get_path(it, fields["author"])), 300),
                language=_norm_lang(_get_path(it, fields["language"])),
                categories=[c for c in cat_list if c][:30],
                image_urls=[image] if isinstance(image, str) and image.startswith("http") else [],
                location_text=truncate(_as_text(_get_path(it, fields["location"])), 300),
            )
        )
    return ParsedFeed(None, None, None, "json_api", entries)


# --- Sitemaps -------------------------------------------------------------------------------
# Google News sitemaps (https://developers.google.com/search/docs/crawling-indexing/sitemaps/news-sitemap)
# carry a title, publication date and language for each recent article, so they work as a feed
# for publishers without RSS. Plain sitemaps only list URLs; their entries have no title unless
# page metadata lookup is enabled for the source.
_SM = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_NEWS = "{http://www.google.com/schemas/sitemap-news/0.9}"
_IMG = "{http://www.google.com/schemas/sitemap-image/1.1}"
PLAIN_SITEMAP_LIMIT = 200


def looks_like_sitemap(content: bytes) -> bool:
    head = content[:2000].lower()
    return b"<urlset" in head or b"<sitemapindex" in head


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(el: ET.Element, name: str) -> str | None:
    """Text of the first direct child with this local name, whatever its namespace."""
    for c in el:
        if _local(c.tag) == name:
            return (c.text or "").strip() or None
    return None


_DATE_ONLY = re.compile(r"^\s*\d{4}-\d{2}-\d{2}\s*$")


def _sitemap_time(value: str | None) -> datetime | None:
    """A W3C datetime. A bare date says nothing about the time of day, so it is not used."""
    if not value or _DATE_ONLY.match(value):
        return None
    return parse_datetime(value)


def _sitemap_day(value: str | None) -> date | None:
    """The day of a bare W3C date (`2026-11-16`), else None."""
    if not value or not _DATE_ONLY.match(value):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def parse_sitemap(content: bytes) -> ParsedFeed:
    head = content[:4000].lower()
    if b"<!doctype" in head or b"<!entity" in head:
        # Sitemaps never need a DTD; refusing them rules out entity-expansion tricks.
        raise FeedParseError("Sitemap contains a DTD, which is not allowed")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise FeedParseError(f"Malformed sitemap: {exc}"[:300]) from exc
    kind = _local(root.tag)
    if kind == "sitemapindex":
        children = []
        for sm in root:
            loc = _child_text(sm, "loc")
            if loc and loc.startswith(("http://", "https://")):
                children.append((loc, parse_datetime(_child_text(sm, "lastmod"))))
        return ParsedFeed(None, None, None, "sitemap_index", [], children=children)
    if kind != "urlset":
        raise FeedParseError(f"Not a sitemap (root element <{kind}>)")

    entries: list[ParsedEntry] = []
    news_count = 0
    languages: set[str] = set()
    for url_el in root:
        loc = _child_text(url_el, "loc")
        if not loc or not loc.startswith(("http://", "https://")):
            continue
        lastmod = _child_text(url_el, "lastmod")
        published = _sitemap_time(lastmod)
        day = _sitemap_day(lastmod)
        title = None
        language = None
        keywords: list[str] = []
        news = url_el.find(f"{_NEWS}news")
        if news is not None:
            news_count += 1
            title = html_to_text(news.findtext(f"{_NEWS}title"))
            pub_date = news.findtext(f"{_NEWS}publication_date")
            published = _sitemap_time(pub_date) or published
            day = _sitemap_day(pub_date) or day
            language = _norm_lang(news.findtext(f"{_NEWS}publication/{_NEWS}language"))
            kw = news.findtext(f"{_NEWS}keywords") or ""
            keywords = [k.strip()[:200] for k in kw.split(",") if k.strip()][:30]
        if language:
            languages.add(language)
        images = [
            (img.findtext(f"{_IMG}loc") or "").strip()
            for img in url_el.findall(f"{_IMG}image")
        ]
        entries.append(
            ParsedEntry(
                title=truncate(title, 1000),
                url=loc.strip(),
                guid=loc.strip(),
                published_at=published,
                published_day=None if published else day,
                language=language,
                categories=keywords,
                image_urls=[u for u in images if u.startswith(("http://", "https://"))][:10],
            )
        )
    warning = None
    if news_count:
        fmt = "news_sitemap"
    else:
        fmt = "sitemap"
        # Plain sitemaps can list a whole archive, oldest first: keep the most recently modified
        # URLs, newest first, so page lookups go to new stories. (Stable: undated keep their order.)
        entries.sort(key=lambda e: e.published_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        entries = entries[:PLAIN_SITEMAP_LIMIT]
        warning = (
            "Plain sitemap without news titles: entries are only collected when page metadata "
            "lookup is enabled for this source (up to 10 new pages per fetch, from the last two days)."
        )
    lang = languages.pop() if len(languages) == 1 else None
    return ParsedFeed(None, None, lang, fmt, entries, warning)


def parse_feed(content: bytes, *, content_type: str | None, source_type: str, api_config: dict | None = None) -> ParsedFeed:
    if source_type == "sitemap" or (source_type != "json" and looks_like_sitemap(content)):
        return parse_sitemap(content)
    stripped = content.lstrip()[:1]
    is_json = source_type == "json" or (content_type or "").lower().split(";")[0].strip().endswith("json") or stripped in (b"{", b"[")
    if is_json:
        return _parse_json(content, api_config)
    return _parse_xml(content, content_type)
