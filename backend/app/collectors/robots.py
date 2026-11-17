"""robots.txt checks following RFC 9309, with a per-host cache.

What RFC 9309 asks, and how this module does it:

* Groups: every group whose user-agent matches our product token (`NEWSWIRE`, case-insensitive)
  applies, merged; only when none matches do the `*` groups apply (also merged).
* Rules: the longest matching `Allow`/`Disallow` path wins, and `Allow` wins a tie. `*` matches
  any run of characters and a trailing `$` anchors the end. (Python's `urllib.robotparser` uses
  the first matching rule and ignores wildcards, so it both blocked feeds a site allows and
  allowed paths a site disallows.)
* 4xx: no robots.txt, everything is allowed. 5xx or a network failure: the file is
  "unreachable" and the crawler must assume complete disallow, but it may keep using a copy it
  read earlier (section 2.4), which this module does for up to `STALE_TTL`.

A failure is retried once, so a single slow TLS handshake no longer pauses a source, and the
verdict says *why* (the real network error), so a connection problem on the NEWSWIRE server
is not mistaken for a publisher blocking it.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

import httpx

from app.core.config import get_settings
from app.core.http import UnsafeURLError, describe_error, safe_request

_lock = threading.Lock()
OK_TTL = 24 * 3600
ERROR_TTL = 10 * 60
STALE_TTL = 7 * 24 * 3600  # how long a previously read robots.txt is used while the site's is unreachable
MAX_BYTES = 512_000
RETRY_DELAY_SECONDS = 1.5


@dataclass
class Rules:
    """Parsed robots.txt."""

    groups: list[tuple[list[str], list[tuple[bool, str]]]] = field(default_factory=list)
    sitemaps: list[str] = field(default_factory=list)

    def rules_for(self, token: str) -> list[tuple[bool, str]]:
        token = token.lower()
        mine = [rules for agents, rules in self.groups if token in agents]
        if not mine:
            mine = [rules for agents, rules in self.groups if "*" in agents]
        return [r for rules in mine for r in rules]

    def allows(self, token: str, url: str) -> bool:
        parts = urlsplit(url)
        path = parts.path or "/"
        if path == "/robots.txt":
            return True
        target = _normalise(path + (f"?{parts.query}" if parts.query else ""))
        best_len, best_allow = -1, True
        for allow, pattern in self.rules_for(token):
            if _matches(pattern, target):
                length = len(pattern)
                if length > best_len or (length == best_len and allow):
                    best_len, best_allow = length, allow
        return best_allow


def _normalise(path: str) -> str:
    # Compare decoded paths so "/%E0%A6%96" and "/খ" are the same rule.
    return unquote(path)


_pattern_cache: dict[str, re.Pattern[str]] = {}


def _matches(pattern: str, target: str) -> bool:
    regex = _pattern_cache.get(pattern)
    if regex is None:
        anchored = pattern.endswith("$")
        body = pattern[:-1] if anchored else pattern
        regex = re.compile("".join(".*" if ch == "*" else re.escape(ch) for ch in body) + ("$" if anchored else ""), re.S)
        if len(_pattern_cache) < 4096:
            _pattern_cache[pattern] = regex
    return regex.match(target) is not None


def parse(text: str) -> Rules:
    rules = Rules()
    agents: list[str] = []
    current: list[tuple[bool, str]] | None = None
    for raw in text.lstrip("﻿").splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if current is not None:  # a user-agent line after rules starts a new group
                agents, current = [], None
            agents.append(value.split("/")[0].strip().lower() or "*")
        elif key in ("allow", "disallow"):
            if not agents:
                continue  # rules before any user-agent line belong to no group
            if current is None:
                current = []
                rules.groups.append((agents, current))
            if value:  # an empty Disallow allows everything; it adds no rule
                current.append((key == "allow", _normalise(value)))
        elif key == "sitemap" and value.startswith(("http://", "https://")):
            rules.sitemaps.append(value)
    return rules


@dataclass
class Verdict:
    allowed: bool
    reason: str | None = None
    # "ok": allowed; "disallowed": the publisher's robots.txt says no;
    # "unreachable": robots.txt could not be read, so fetching pauses (RFC 9309 2.3.1.4).
    kind: str = "ok"


@dataclass
class _Entry:
    expires: float
    rules: Rules | None  # None with allow_all False: unreachable
    allow_all: bool
    error: str | None = None
    good_rules: Rules | None = None  # last successfully read copy, for when the site is unreachable
    good_allow_all: bool = False
    good_until: float = 0.0


_cache: dict[str, _Entry] = {}


def agent_token() -> str:
    return get_settings().user_agent.split("/")[0].strip() or "NEWSWIRE"


def _fetch(client: httpx.Client, origin: str) -> tuple[Rules | None, bool, str | None]:
    """(rules, allow_all, error). error is set when robots.txt is unreachable."""
    last_error = "robots.txt could not be retrieved"
    for attempt in range(2):
        if attempt:
            time.sleep(RETRY_DELAY_SECONDS)
        try:
            resp = safe_request(client, "GET", origin + "/robots.txt", max_bytes=MAX_BYTES,
                                headers={"Accept": "text/plain, */*;q=0.5"})
        except UnsafeURLError as exc:
            return None, False, describe_error(exc)  # not transient: retrying cannot help
        except Exception as exc:  # network failure
            last_error = describe_error(exc)
            continue
        if resp.status_code >= 500:
            last_error = f"robots.txt returned HTTP {resp.status_code}"
            continue
        if resp.status_code == 429:
            last_error = "robots.txt returned HTTP 429 (too many requests)"
            continue
        if 400 <= resp.status_code < 500:
            return None, True, None
        return parse(resp.text), False, None
    return None, False, last_error


def check(client: httpx.Client, url: str) -> Verdict:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    now = time.monotonic()
    with _lock:
        entry = _cache.get(origin)
    if entry is None or entry.expires <= now:
        rules, allow_all, error = _fetch(client, origin)
        if error is None:
            entry = _Entry(now + OK_TTL, rules, allow_all, None, rules, allow_all, now + OK_TTL + STALE_TTL)
        else:
            previous = entry
            entry = _Entry(now + ERROR_TTL, None, False, error)
            if previous is not None and previous.good_until > now:
                entry.good_rules, entry.good_allow_all, entry.good_until = (
                    previous.good_rules, previous.good_allow_all, previous.good_until)
        with _lock:
            _cache[origin] = entry

    rules, allow_all = entry.rules, entry.allow_all
    if entry.error is not None:
        if entry.good_until > now and (entry.good_rules is not None or entry.good_allow_all):
            rules, allow_all = entry.good_rules, entry.good_allow_all  # RFC 9309 2.4: use the cached copy
        else:
            return Verdict(
                False,
                f"Could not read {parts.netloc}/robots.txt ({entry.error}). Fetching from this site is "
                f"paused until it can be read; this is a connection problem, not the publisher blocking NEWSWIRE.",
                "unreachable",
            )
    if allow_all or rules is None:
        return Verdict(True)
    if rules.allows(agent_token(), url):
        return Verdict(True)
    return Verdict(False, "Disallowed by the publisher's robots.txt", "disallowed")


def is_allowed(client: httpx.Client, url: str) -> tuple[bool, str | None]:
    """(allowed, reason). Kept for callers that do not need to tell the two kinds of 'no' apart."""
    v = check(client, url)
    return v.allowed, v.reason


def sitemaps(client: httpx.Client, url: str) -> list[str]:
    """Sitemap URLs the publisher lists in robots.txt (reads the same cache as `check`)."""
    check(client, url)
    parts = urlsplit(url)
    with _lock:
        entry = _cache.get(f"{parts.scheme}://{parts.netloc}")
    if entry is None:
        return []
    rules = entry.rules or entry.good_rules
    return list(rules.sitemaps) if rules is not None else []


def clear_cache() -> None:
    with _lock:
        _cache.clear()
