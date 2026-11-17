"""Openverse and Wikimedia Commons search clients.

Openverse: https://api.openverse.org/v1/ - anonymous use is rate limited; registering a free
OAuth application (NEWSWIRE_OPENVERSE_CLIENT_ID/SECRET) raises the limit.
Wikimedia Commons: MediaWiki API; Wikimedia asks clients to send a descriptive User-Agent with
contact details (NEWSWIRE_CONTACT).
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from app.collectors.parsers import html_to_text
from app.core.config import get_settings
from app.core.http import make_client, safe_request
from app.images.licenses import normalize_license


class ProviderError(Exception):
    pass


@dataclass
class ImageCandidate:
    provider: str
    provider_id: str
    title: str | None
    creator: str | None
    creator_url: str | None
    source_page_url: str | None
    original_url: str | None
    thumbnail_url: str | None
    width: int | None
    height: int | None
    mime: str | None
    license_code: str
    license_version: str
    license_raw: str | None
    license_url: str | None
    attribution_text: str | None
    repository: str | None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def build_attribution(title: str | None, creator: str | None, license_name: str | None, source: str | None) -> str:
    parts = []
    parts.append(f"“{title}”" if title else "Image")
    if creator:
        parts.append(f"by {creator}")
    if source:
        parts.append(f"via {source}")
    text = " ".join(parts)
    if license_name:
        text += f", licensed under {license_name}"
    return text


_token_lock = threading.Lock()
_token: dict[str, Any] = {"value": None, "expires": 0.0}


def _openverse_token(client: httpx.Client) -> str | None:
    s = get_settings()
    if not (s.openverse_client_id and s.openverse_client_secret):
        return None
    with _token_lock:
        if _token["value"] and _token["expires"] > time.monotonic() + 60:
            return _token["value"]
    resp = safe_request(
        client, "POST", "https://api.openverse.org/v1/auth_tokens/token/",
        data={"grant_type": "client_credentials", "client_id": s.openverse_client_id,
              "client_secret": s.openverse_client_secret},
        max_bytes=100_000,
    )
    if resp.status_code != 200:
        return None
    data = resp.json()
    with _token_lock:
        _token.update(value=data.get("access_token"), expires=time.monotonic() + int(data.get("expires_in", 3600)))
    return _token["value"]


def search_openverse(
    query: str, *, page: int = 1, page_size: int = 20, commercial_only: bool = False,
    modification_allowed: bool = False, client: httpx.Client | None = None,
) -> dict:
    own = client is None
    client = client or make_client(timeout=20)
    try:
        params: dict[str, Any] = {"q": query, "page": page, "page_size": min(page_size, 20), "mature": "false"}
        lt = [x for x, on in (("commercial", commercial_only), ("modification", modification_allowed)) if on]
        if lt:
            params["license_type"] = ",".join(lt)
        headers = {"Accept": "application/json"}
        token = _openverse_token(client)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        url = str(httpx.URL("https://api.openverse.org/v1/images/", params=params))
        resp = safe_request(client, "GET", url, headers=headers, max_bytes=3_000_000)
        if resp.status_code == 429:
            raise ProviderError("Openverse rate limit reached; try again later or configure API credentials")
        if resp.status_code != 200:
            raise ProviderError(f"Openverse returned HTTP {resp.status_code}")
        data = resp.json()
    except httpx.HTTPError as exc:
        raise ProviderError(f"Openverse request failed: {exc}") from exc
    finally:
        if own:
            client.close()
    results = []
    for r in data.get("results", []):
        code, ver = normalize_license(r.get("license"), r.get("license_version"), r.get("license_url"))
        results.append(
            ImageCandidate(
                provider="openverse",
                provider_id=str(r.get("id")),
                title=r.get("title"),
                creator=r.get("creator"),
                creator_url=r.get("creator_url"),
                source_page_url=r.get("foreign_landing_url"),
                original_url=r.get("url"),
                thumbnail_url=r.get("thumbnail"),
                width=r.get("width"),
                height=r.get("height"),
                mime=(f"image/{r['filetype']}" if r.get("filetype") else None),
                license_code=code,
                license_version=ver,
                license_raw=" ".join(x for x in (r.get("license"), r.get("license_version")) if x) or None,
                license_url=r.get("license_url"),
                attribution_text=r.get("attribution") or build_attribution(r.get("title"), r.get("creator"), None, r.get("source")),
                repository=r.get("source") or r.get("provider"),
                metadata={"provider": r.get("provider"), "source": r.get("source"), "tags": [t.get("name") for t in (r.get("tags") or [])][:20]},
            )
        )
    return {"provider": "openverse", "page": page, "total": data.get("result_count"), "page_count": data.get("page_count"), "results": results}


def _ext(meta: dict, key: str) -> str | None:
    v = (meta.get(key) or {}).get("value")
    return html_to_text(v) if isinstance(v, str) else None


def search_wikimedia(query: str, *, page: int = 1, page_size: int = 20, client: httpx.Client | None = None) -> dict:
    own = client is None
    client = client or make_client(timeout=20)
    params = {
        "action": "query", "format": "json", "formatversion": "2", "generator": "search",
        "gsrsearch": f"filetype:bitmap {query}", "gsrnamespace": "6", "gsrlimit": str(min(page_size, 50)),
        "gsroffset": str((page - 1) * page_size), "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata", "iiurlwidth": "480",
    }
    try:
        url = str(httpx.URL("https://commons.wikimedia.org/w/api.php", params=params))
        resp = safe_request(client, "GET", url, headers={"Accept": "application/json"}, max_bytes=5_000_000)
        if resp.status_code != 200:
            raise ProviderError(f"Wikimedia Commons returned HTTP {resp.status_code}")
        data = resp.json()
    except httpx.HTTPError as exc:
        raise ProviderError(f"Wikimedia Commons request failed: {exc}") from exc
    finally:
        if own:
            client.close()
    pages = sorted((data.get("query") or {}).get("pages", []), key=lambda p: p.get("index", 0))
    results = []
    for p in pages:
        info = (p.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        lic_short = _ext(meta, "LicenseShortName")
        lic_url = _ext(meta, "LicenseUrl")
        code, ver = normalize_license(lic_short, None, lic_url)
        if (_ext(meta, "Copyrighted") or "").lower() == "false" and code == "unknown":
            code, ver = "pdm", ""
        artist = _ext(meta, "Artist")
        title = _ext(meta, "ObjectName") or (p.get("title") or "").removeprefix("File:")
        credit = _ext(meta, "Credit")
        attribution = build_attribution(title, artist, lic_short, "Wikimedia Commons")
        results.append(
            ImageCandidate(
                provider="wikimedia",
                provider_id=str(p.get("pageid")),
                title=title,
                creator=artist,
                creator_url=None,
                source_page_url=info.get("descriptionurl"),
                original_url=info.get("url"),
                thumbnail_url=info.get("thumburl"),
                width=info.get("width"),
                height=info.get("height"),
                mime=info.get("mime"),
                license_code=code,
                license_version=ver,
                license_raw=lic_short,
                license_url=lic_url,
                attribution_text=attribution,
                repository="Wikimedia Commons",
                metadata={"credit": credit, "usage_terms": _ext(meta, "UsageTerms"),
                          "attribution_required": _ext(meta, "AttributionRequired"),
                          "restrictions": _ext(meta, "Restrictions"), "file_title": p.get("title")},
            )
        )
    has_more = "continue" in data
    return {"provider": "wikimedia", "page": page, "total": None, "page_count": page + 1 if has_more else page, "results": results}
