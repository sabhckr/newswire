"""URL normalisation for level-1 deduplication.

Two URLs that differ only in tracking parameters, fragment, default port, host case,
`www.` prefix, trailing slash or query-parameter order are the same article.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = frozenset(
    {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
        "utm_name", "utm_reader", "utm_brand", "utm_social", "utm_social-type",
        "fbclid", "gclid", "dclid", "msclkid", "yclid", "mc_cid", "mc_eid", "igshid",
        "ref", "ref_src", "ref_url", "cmpid", "cmp", "ito", "ns_mchannel", "ns_source",
        "ns_campaign", "ns_linkname", "ns_fee", "at_medium", "at_campaign", "at_custom1",
        "rss", "rssfeed", "feed", "src", "spm", "_ga", "_gl", "share", "via", "smid",
        "partner", "taid", "outputtype", "ocid", "cid",
    }
)


def normalize_url(url: str) -> str:
    """Return a canonical comparison key for a URL. Raises ValueError for non-http(s) URLs."""
    if not url:
        raise ValueError("empty url")
    url = url.strip()
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"unsupported scheme: {scheme!r}")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise ValueError("url has no host")
    if host.startswith("www."):
        host = host[4:]
    port = parts.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"

    path = quote(unquote(parts.path or "/"), safe="/:@!$&'()*+,;=-._~%")
    while "//" in path:
        path = path.replace("//", "/")
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    for suffix in ("/index.html", "/index.htm", "/index.php"):
        if path.endswith(suffix):
            path = path[: -len(suffix)] or "/"

    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in TRACKING_PARAMS and not k.lower().startswith("utm_")
    ]
    query_pairs.sort()
    query = urlencode(query_pairs, doseq=True)

    # Scheme is folded to https for comparison: the same article is often served on both.
    return urlunsplit(("https", netloc, path, query, ""))


def safe_normalize(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return normalize_url(url)
    except ValueError:
        return None


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()
