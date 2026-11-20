"""Outbound HTTP with SSRF protection.

Every request to a user-supplied URL (feeds, API sources, image downloads, publishing
endpoints) goes through `safe_request`:

* only http/https, no credentials embedded in the URL, sane ports;
* the host's resolved addresses are checked against private, loopback, link-local,
  multicast, reserved and unspecified ranges *at connect time*, inside the transport,
  so DNS rebinding between a check and the connection is not possible;
* redirects are followed manually and every hop is re-validated;
* environment proxies are ignored and response bodies are size-capped.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx

from app.core.config import get_settings


class UnsafeURLError(ValueError):
    """The URL is not allowed (scheme, credentials, or a non-public destination)."""


class ResponseTooLarge(Exception):
    pass


class BlockedDestination(httpcore.ConnectError):
    """Raised inside the transport when a host resolves to a non-public address."""


def is_public_address(addr: str) -> bool:
    ip = ipaddress.ip_address(addr)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(ip.is_global) and not ip.is_multicast and not ip.is_unspecified


def validate_url(url: str, *, allow_private: bool | None = None) -> str:
    """Static checks that do not need DNS. Returns the URL unchanged when acceptable."""
    if allow_private is None:
        allow_private = get_settings().allow_private_network
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise UnsafeURLError(f"Invalid URL: {exc}") from exc
    if parts.scheme not in ("http", "https"):
        raise UnsafeURLError("Only http and https URLs are allowed")
    if parts.username or parts.password:
        raise UnsafeURLError("URLs with embedded credentials are not allowed")
    host = parts.hostname
    if not host:
        raise UnsafeURLError("URL has no host")
    try:
        port = parts.port
    except ValueError as exc:
        raise UnsafeURLError("Invalid port") from exc
    if port is not None and not (1 <= port <= 65535):
        raise UnsafeURLError("Invalid port")
    if allow_private:
        return url
    lowered = host.lower().rstrip(".")
    if lowered in ("localhost",) or lowered.endswith((".localhost", ".local", ".internal")):
        raise UnsafeURLError("Destination is a local or internal host")
    try:
        literal = ipaddress.ip_address(lowered.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None and not is_public_address(str(literal)):
        raise UnsafeURLError("Destination address is not public")
    return url


def resolve_public(host: str, port: int, allow_private: bool) -> list[tuple]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise httpcore.ConnectError(f"DNS lookup failed for {host}: {exc}") from exc
    if not infos:
        raise httpcore.ConnectError(f"DNS lookup returned no addresses for {host}")
    if not allow_private:
        for info in infos:
            addr = info[4][0]
            if not is_public_address(addr):
                raise BlockedDestination(
                    f"Blocked destination: {host} resolves to {addr}, a non-public address. "
                    + non_public_hint(addr)
                )
    return infos


_FAKE_IP = ipaddress.ip_network("198.18.0.0/15")


def non_public_hint(addr: str) -> str:
    """Why a news site might resolve to a non-public address, in words an operator can act on."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return ""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip in _FAKE_IP:
        return ("198.18.0.0/15 is the range VPN and proxy apps (Clash, sing-box, Surge, Shadowrocket and "
                "others) hand out in fake-IP mode, so every site looks blocked. Switch the VPN to "
                "real-IP/redir-host mode or exclude this server from it.")
    if ip.is_loopback or ip.is_unspecified:
        return ("The site's own DNS, or a DNS filter or ad blocker on this network, points it at this "
                "machine. If every site shows this, check this server's DNS settings.")
    if ip.is_private:
        return "A local DNS server, VPN or proxy on this network is answering for the site."
    return ""


class GuardedBackend(httpcore.SyncBackend):
    def __init__(self, allow_private: bool):
        super().__init__()
        self.allow_private = allow_private

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        infos = resolve_public(host, port, self.allow_private)
        last_exc: Exception | None = None
        for family, socktype, proto, _canon, sockaddr in infos:
            sock = socket.socket(family, socktype, proto)
            try:
                sock.settimeout(timeout)
                if local_address:
                    sock.bind((local_address, 0))
                sock.connect(sockaddr)
                for option in socket_options or []:
                    sock.setsockopt(*option)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                from httpcore._backends.sync import SyncStream

                return SyncStream(sock)
            except socket.timeout as exc:
                sock.close()
                last_exc = httpcore.ConnectTimeout(str(exc))
            except OSError as exc:
                sock.close()
                last_exc = httpcore.ConnectError(str(exc))
        assert last_exc is not None
        raise last_exc


def make_client(
    *,
    timeout: float | None = None,
    allow_private: bool | None = None,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    settings = get_settings()
    if allow_private is None:
        allow_private = settings.allow_private_network
    if transport is None:
        guarded = httpx.HTTPTransport(retries=0)
        # httpx does not expose the network backend; swap it on the underlying pool.
        guarded._pool._network_backend = GuardedBackend(allow_private)  # type: ignore[attr-defined]
        transport = guarded
    return httpx.Client(
        transport=transport,
        timeout=httpx.Timeout(timeout or settings.fetch_timeout_seconds, connect=10.0),
        follow_redirects=False,
        trust_env=False,
        headers={"User-Agent": settings.full_user_agent},
    )


@dataclass
class SafeResponse:
    status_code: int
    headers: httpx.Headers
    content: bytes
    url: str
    redirects: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return self.content.decode(self.encoding or "utf-8", errors="replace")

    @property
    def encoding(self) -> str | None:
        ctype = self.headers.get("content-type", "")
        for part in ctype.split(";"):
            part = part.strip()
            if part.lower().startswith("charset="):
                return part.split("=", 1)[1].strip("\"' ") or None
        return None

    def json(self) -> Any:
        import json

        return json.loads(self.content)


REDIRECT_CODES = {301, 302, 303, 307, 308}


def safe_request(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    max_bytes: int | None = None,
    max_redirects: int = 5,
    allow_private: bool | None = None,
    follow_redirects: bool = True,
    **kwargs: Any,
) -> SafeResponse:
    """follow_redirects=False returns 3xx responses as they are (YouTube's 308 means "upload incomplete")."""
    max_bytes = max_bytes or get_settings().max_feed_bytes
    redirects: list[str] = []
    current = url
    for _ in range(max_redirects + 1):
        validate_url(current, allow_private=allow_private)
        with client.stream(method, current, headers=headers, **kwargs) as resp:
            if follow_redirects and resp.status_code in REDIRECT_CODES and resp.headers.get("location"):
                redirects.append(current)
                current = urljoin(current, resp.headers["location"])
                if resp.status_code == 303:
                    method, kwargs = "GET", {}
                continue
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise ResponseTooLarge(f"Response is {declared} bytes (limit {max_bytes})")
            chunks: list[bytes] = []
            size = 0
            for chunk in resp.iter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise ResponseTooLarge(f"Response exceeded {max_bytes} bytes")
                chunks.append(chunk)
            return SafeResponse(resp.status_code, resp.headers, b"".join(chunks), current, redirects)
    raise httpx.TooManyRedirects(f"More than {max_redirects} redirects")


def describe_error(exc: Exception) -> str:
    """Short, log-safe description of an outbound request failure."""
    if isinstance(exc, UnsafeURLError):
        return f"Blocked URL: {exc}"
    if isinstance(exc, httpx.ConnectTimeout):
        return "Connection timed out (the site, or this server's internet connection, did not answer in time)"
    if isinstance(exc, httpx.TimeoutException):
        return "Request timed out (the site answered too slowly)"
    if isinstance(exc, httpx.ConnectError):
        msg = str(exc)
        if msg.startswith("Blocked destination"):
            return msg[:600]
        if "CERTIFICATE_VERIFY_FAILED" in msg:
            return ("The site's HTTPS certificate is not valid (" + msg.split("certificate verify failed:")[-1].split("(_ssl")[0].strip()
                    + "). Samyabadi does not skip certificate checks; the publisher has to fix it.")[:300]
        if msg.startswith("DNS lookup failed"):
            return (msg + " (the name does not exist, or this server's DNS is not working)")[:300]
        return f"Connection failed: {msg}"[:300]
    if isinstance(exc, ResponseTooLarge):
        return str(exc)
    if isinstance(exc, httpx.TooManyRedirects):
        return "Too many redirects"
    return f"{type(exc).__name__}: {exc}"[:300]


def looks_like_bot_check(resp: SafeResponse) -> bool:
    """A bot-protection interstitial (Cloudflare, Sucuri, Imperva, DDoS-Guard and similar)
    rather than the page or feed itself."""
    h = resp.headers
    if h.get("cf-mitigated", "").lower() == "challenge":
        return True
    head = resp.content[:6000].lower()
    markers = (b"just a moment...", b"challenge-platform", b"cf-browser-verification", b"attention required! | cloudflare",
               b"sucuri website firewall", b"_incapsula_resource", b"ddos-guard", b"checking your browser",
               b"enable javascript and cookies to continue", b"captcha")
    return resp.status_code in (403, 429, 503) and any(m in head for m in markers)


def describe_http_status(resp: SafeResponse) -> str:
    """A short explanation of a non-2xx answer, honest about who is refusing and why."""
    code = resp.status_code
    if looks_like_bot_check(resp):
        server = resp.headers.get("server", "").split("/")[0] or "the publisher's bot protection"
        return (f"HTTP {code}: the publisher's bot protection ({server}) asked for a browser check. "
                "Samyabadi does not get around these checks; ask the publisher to allow its "
                "user agent, or use another feed from the same paper.")
    if code in (401, 402):
        return f"HTTP {code} (login or payment required; Samyabadi does not bypass access controls)"
    if code == 403:
        return (f"HTTP 403: the site refused the request (access restricted; Samyabadi does not "
                "bypass access controls). Sites sometimes refuse whole hosting providers' networks; "
                "if every site does this, the server's IP address may be blocked.")
    if code == 404:
        return "HTTP 404: the address no longer exists (the feed may have moved; try Find feeds)"
    if code == 429:
        return "HTTP 429: the site asked Samyabadi to slow down; it will wait before trying again"
    if code >= 500:
        return f"HTTP {code}: the site had a server error"
    return f"HTTP {code}"

