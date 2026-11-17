"""Embedded videos in article bodies: YouTube, Facebook, Vimeo and Dailymotion players only.

The editor turns a pasted video link into the provider's embed address; the sanitiser keeps an
<iframe> only when its address is one of these players, so an article can never frame anything else.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, quote, urlsplit

_EMBED = [
    re.compile(r"^https://www\.youtube(?:-nocookie)?\.com/embed/[A-Za-z0-9_-]{6,20}(?:\?[A-Za-z0-9_=&;.%-]*)?$"),
    re.compile(r"^https://player\.vimeo\.com/video/\d{1,15}(?:\?[A-Za-z0-9_=&;.%-]*)?$"),
    re.compile(r"^https://www\.facebook\.com/plugins/video\.php\?[A-Za-z0-9_=&;.%/:+-]+$"),
    re.compile(r"^https://www\.dailymotion\.com/embed/video/[A-Za-z0-9]{4,20}(?:\?[A-Za-z0-9_=&;.%-]*)?$"),
]
FRAME_SOURCES = "https://www.youtube-nocookie.com https://www.youtube.com https://player.vimeo.com https://www.facebook.com https://www.dailymotion.com"


def is_embed_url(url: str | None) -> bool:
    return bool(url) and any(p.match(url) for p in _EMBED)


def embed_url(url: str) -> str | None:
    """The player address for a video page link, or None when the link is not a supported video."""
    url = (url or "").strip()
    if is_embed_url(url):
        return url
    try:
        parts = urlsplit(url if "://" in url else f"https://{url}")
    except ValueError:
        return None
    host = (parts.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    path = parts.path
    if host in ("youtube.com", "youtube-nocookie.com", "music.youtube.com"):
        vid = parse_qs(parts.query).get("v", [None])[0]
        m = re.match(r"^/(?:shorts|live|embed)/([A-Za-z0-9_-]{6,20})", path)
        vid = vid or (m.group(1) if m else None)
        return f"https://www.youtube-nocookie.com/embed/{vid}" if vid and re.fullmatch(r"[A-Za-z0-9_-]{6,20}", vid) else None
    if host == "youtu.be":
        vid = path.strip("/").split("/")[0]
        return f"https://www.youtube-nocookie.com/embed/{vid}" if re.fullmatch(r"[A-Za-z0-9_-]{6,20}", vid) else None
    if host == "vimeo.com":
        m = re.match(r"^/(\d{1,15})", path)
        return f"https://player.vimeo.com/video/{m.group(1)}" if m else None
    if host in ("dailymotion.com", "dai.ly"):
        m = re.match(r"^/(?:video/)?([A-Za-z0-9]{4,20})", path)
        return f"https://www.dailymotion.com/embed/video/{m.group(1)}" if m else None
    if host in ("facebook.com", "fb.watch", "web.facebook.com"):
        if host == "fb.watch" or "/videos/" in path or path.startswith(("/watch", "/reel/")):
            clean = f"https://{'fb.watch' if host == 'fb.watch' else 'www.facebook.com'}{path}"
            if parts.query and path.startswith("/watch"):
                clean += f"?{parts.query}"
            return f"https://www.facebook.com/plugins/video.php?href={quote(clean, safe='')}&show_text=false"
    return None
