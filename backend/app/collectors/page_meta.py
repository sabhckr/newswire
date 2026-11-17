"""Optional, per-source article page metadata (canonical URL, OpenGraph image, description).

Only runs for sources where an editor enabled `extract_page_metadata`, respects robots.txt,
never sends credentials or cookies, and stops at 401/402/403 (paywalls and access controls
are not bypassed). Only metadata is stored, never the article body.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

from app.collectors import robots
from app.collectors.parsers import parse_datetime
from app.core.http import safe_request


@dataclass
class PageMetadata:
    title: str | None = None
    canonical_url: str | None = None
    image_url: str | None = None
    description: str | None = None
    published_at: object | None = None
    language: str | None = None
    og_type: str | None = None  # "article" for news stories; "website" for section and home pages
    site_name: str | None = None

    @property
    def is_article(self) -> bool:
        return self.published_at is not None or (self.og_type or "").lower() == "article"


def fetch_page_metadata(client: httpx.Client, url: str, respect_robots: bool = True) -> PageMetadata | None:
    if respect_robots:
        allowed, _ = robots.is_allowed(client, url)
        if not allowed:
            return None
    try:
        resp = safe_request(client, "GET", url, max_bytes=2_000_000, headers={"Accept": "text/html"})
    except Exception:
        return None
    if resp.status_code != 200 or "html" not in resp.headers.get("content-type", ""):
        return None
    soup = BeautifulSoup(resp.content, "html.parser")
    meta = PageMetadata()
    link = soup.find("link", rel="canonical")
    if link and link.get("href", "").startswith("http"):
        meta.canonical_url = link["href"].strip()

    def prop(name: str) -> str | None:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        return tag.get("content").strip() if tag and tag.get("content") else None

    title = prop("og:title") or (soup.title.string.strip() if soup.title and soup.title.string else None)
    meta.title = title[:1000] if title else None
    img = prop("og:image")
    if img and img.startswith("http"):
        meta.image_url = img
    meta.description = prop("og:description") or prop("description")
    meta.published_at = parse_datetime(prop("article:published_time"))
    meta.og_type = (prop("og:type") or "")[:40] or None
    meta.site_name = (prop("og:site_name") or "")[:200] or None
    html = soup.find("html")
    if html and html.get("lang"):
        meta.language = html["lang"].lower()[:16]
    return meta


_ID_IN_PATH = re.compile(r"\d{4,}")


def looks_like_article(url: str, meta: PageMetadata) -> bool:
    """A story page rather than a section, tag or home page: it says so (og:type article or a
    publication time) or its address carries a story number, as most news sites' do."""
    return meta.is_article or bool(_ID_IN_PATH.search(urlsplit(url).path))


# " | Site", " - Site", " – Site", " :: Site", ": Site" at the end of a page title.
_TITLE_SEP = re.compile(r"\s+(?:\|\|?|[-\u2013\u2014]|::)\s+|\s*:\s+")


def _key(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def _host_key(url: str) -> str:
    labels = (urlsplit(url).hostname or "").split(".")[:-1]  # drop the top-level domain
    labels = [lb for lb in labels if lb not in ("www", "com", "net", "org", "co", "gov", "ac", "edu")]
    return max(labels, key=len, default="")


def clean_page_title(title: str, url: str, site_name: str | None = None) -> str:
    """Drop a trailing site name from a page title ("Headline - MZamin" -> "Headline").

    Only when the last segment names the site (its address or og:site_name), so headlines
    that contain a colon or dash themselves are left alone."""
    matches = list(_TITLE_SEP.finditer(title))
    if not matches:
        return title
    m = matches[-1]
    head, tail = title[: m.start()].strip(), title[m.end():].strip()
    if not head or len(tail) > 80:
        return title
    tail_key = _key(tail)
    names = {k for k in (_host_key(url).replace("-", ""), _key(site_name or "")) if len(k) >= 4}
    if tail_key and any(k in tail_key or (len(tail_key) >= 4 and tail_key in k) for k in names):
        return head
    return title
