"""robots.txt checks (RFC 9309 behaviour) with a per-host cache."""

from __future__ import annotations

import threading
import time
import urllib.robotparser
from urllib.parse import urlsplit

import httpx

from app.core.config import get_settings
from app.core.http import safe_request

_lock = threading.Lock()
_cache: dict[str, tuple[float, urllib.robotparser.RobotFileParser | None, bool]] = {}
OK_TTL = 24 * 3600
ERROR_TTL = 15 * 60


def _agent() -> str:
    return get_settings().user_agent.split("/")[0]


def is_allowed(client: httpx.Client, url: str) -> tuple[bool, str | None]:
    """Returns (allowed, reason). Unreachable robots.txt (5xx) is treated as a full disallow."""
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    now = time.monotonic()
    with _lock:
        cached = _cache.get(origin)
    if cached and cached[0] > now:
        _, parser, allow_all = cached
    else:
        parser, allow_all, ttl = None, False, OK_TTL
        try:
            resp = safe_request(client, "GET", origin + "/robots.txt", max_bytes=512_000)
            if resp.status_code >= 500:
                with _lock:
                    _cache[origin] = (now + ERROR_TTL, None, False)
                return False, f"robots.txt returned HTTP {resp.status_code}; fetching paused until it is reachable"
            if 400 <= resp.status_code < 500:
                allow_all = True
            else:
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(resp.text.splitlines())
        except Exception:  # network failure: RFC 9309 says assume complete disallow
            with _lock:
                _cache[origin] = (now + ERROR_TTL, None, False)
            return False, "robots.txt could not be retrieved; fetching paused until it is reachable"
        with _lock:
            _cache[origin] = (now + ttl, parser, allow_all)
    if allow_all:
        return True, None
    if parser is None:
        return False, "robots.txt unavailable"
    if parser.can_fetch(_agent(), url):
        return True, None
    return False, "Disallowed by the publisher's robots.txt"


def clear_cache() -> None:
    with _lock:
        _cache.clear()
