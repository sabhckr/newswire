"""Optional, per-source article page metadata (canonical URL, OpenGraph image, description).

Only runs for sources where an editor enabled `extract_page_metadata`, respects robots.txt,
never sends credentials or cookies, and stops at 401/402/403 (paywalls and access controls
are not bypassed). Only metadata is stored, never the article body.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup

from app.collectors import robots
from app.collectors.parsers import parse_datetime
from app.core.http import safe_request


@dataclass
class PageMetadata:
    canonical_url: str | None = None
    image_url: str | None = None
    description: str | None = None
    published_at: object | None = None
    language: str | None = None


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

    img = prop("og:image")
    if img and img.startswith("http"):
        meta.image_url = img
    meta.description = prop("og:description") or prop("description")
    meta.published_at = parse_datetime(prop("article:published_time"))
    html = soup.find("html")
    if html and html.get("lang"):
        meta.language = html["lang"].lower()[:16]
    return meta
