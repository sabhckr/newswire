"""Publishing connectors: built-in public API, WordPress REST API, signed webhook."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.articles.content import credit_line, render_article_html
from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.core.http import safe_request
from app.models import Article, ArticlePublication, PublishingDestination


class PublishError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class RemoteResult:
    remote_id: str | None
    remote_url: str | None
    remote_status: str
    details: dict[str, Any] = field(default_factory=dict)


def article_payload(article: Article) -> dict:
    img = article.featured_image
    base = get_settings().public_base_url.rstrip("/")
    return {
        "id": str(article.id),
        "slug": article.slug,
        "headline": article.headline,
        "subtitle": article.subtitle,
        "excerpt": article.excerpt,
        "seo_title": article.seo_title,
        "seo_description": article.seo_description,
        "category": article.category.name if article.category else None,
        "tags": [t.name for t in article.tags],
        "html": render_article_html(article),
        "body_html": render_article_html(article, include_header=False, include_featured=False),
        "featured_image": (
            {
                "url": f"{base}/media/{img.local_path}" if img.local_path else None,
                "caption": article.featured_image_caption,
                "credit": credit_line(img),
                "license": img.license.name if img.license else None,
                "license_url": img.license_url,
                "source_page_url": img.source_page_url,
            }
            if img
            else None
        ),
        "sources": [{"title": s.title, "url": s.url, "publisher": s.publisher} for s in article.sources],
        "version": article.version,
        "published_at": article.published_at.isoformat() if article.published_at else None,
        "updated_at": article.updated_at.isoformat() if article.updated_at else None,
    }


class BuiltinConnector:
    """Publishes to NEWSWIRE's own read-only public API (/api/v1/public/articles)."""

    def __init__(self, dest: PublishingDestination):
        self.dest = dest

    def _url(self, article: Article) -> str:
        return f"{get_settings().public_base_url.rstrip('/')}/api/v1/public/articles/{article.slug}"

    def publish(self, article, publication, mode, client) -> RemoteResult:
        return RemoteResult(str(article.id), self._url(article), "draft" if mode == "draft" else "published")

    def unpublish(self, article, publication, client) -> RemoteResult:
        return RemoteResult(str(article.id), None, "unpublished")

    def status(self, article, publication, client) -> RemoteResult:
        return RemoteResult(publication.remote_id, publication.remote_url, publication.remote_status)

    def check(self, client) -> str:
        return "Built-in public API is always available"


class WordPressConnector:
    def __init__(self, dest: PublishingDestination):
        if not dest.base_url or not dest.username:
            raise PublishError("WordPress destination needs a site URL and a username", retryable=False)
        password = decrypt_secret(dest.secret_encrypted)
        if not password:
            raise PublishError("WordPress application password is missing", retryable=False)
        self.dest = dest
        self.api = dest.base_url.rstrip("/") + "/wp-json/wp/v2"
        token = base64.b64encode(f"{dest.username}:{password}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "Accept": "application/json"}

    def _call(self, client, method, path, *, json_body=None, content=None, extra_headers=None, max_bytes=5_000_000):
        headers = dict(self.headers)
        if extra_headers:
            headers.update(extra_headers)
        kwargs: dict[str, Any] = {}
        if json_body is not None:
            kwargs["content"] = json.dumps(json_body).encode()
            headers["Content-Type"] = "application/json"
        elif content is not None:
            kwargs["content"] = content
        resp = safe_request(client, method, self.api + path, headers=headers, max_bytes=max_bytes, **kwargs)
        if resp.status_code in (401, 403):
            raise PublishError(f"WordPress rejected the credentials (HTTP {resp.status_code})", retryable=False)
        if resp.status_code == 404:
            raise PublishError("WordPress endpoint or post not found (HTTP 404)", retryable=False)
        if resp.status_code >= 400:
            msg = ""
            try:
                msg = resp.json().get("message", "")
            except Exception:
                pass
            raise PublishError(f"WordPress HTTP {resp.status_code} {msg}"[:300], retryable=resp.status_code >= 500 or resp.status_code == 429)
        try:
            return resp.json()
        except ValueError as exc:
            raise PublishError("WordPress returned a non-JSON response") from exc

    def _featured_media(self, article: Article, publication: ArticlePublication | None, client) -> int | None:
        img = article.featured_image
        if img is None or not img.local_path:
            return None
        details = (publication.details or {}) if publication else {}
        cached = (details.get("media") or {}).get(str(img.id))
        if cached:
            return int(cached)
        path = get_settings().images_dir / img.local_path
        data = path.read_bytes()
        media = self._call(
            client, "POST", "/media", content=data,
            extra_headers={"Content-Type": img.mime or "image/jpeg",
                           "Content-Disposition": f'attachment; filename="{path.name}"'},
            max_bytes=2_000_000,
        )
        credit = credit_line(img)
        self._call(client, "POST", f"/media/{media['id']}", json_body={
            "caption": f"{article.featured_image_caption or ''} {credit}".strip(),
            "alt_text": article.featured_image_caption or img.title or "",
            "description": credit,
        })
        return int(media["id"])

    def publish(self, article, publication, mode, client) -> RemoteResult:
        media_id = self._featured_media(article, publication, client)
        body = {
            "title": article.headline,
            "content": render_article_html(article, include_header=False, include_featured=False),
            "excerpt": article.excerpt or article.seo_description or "",
            "slug": article.slug,
            "status": "draft" if mode == "draft" else "publish",
        }
        if media_id:
            body["featured_media"] = media_id
        if publication and publication.remote_id:
            post = self._call(client, "POST", f"/posts/{publication.remote_id}", json_body=body)
        else:
            post = self._call(client, "POST", "/posts", json_body=body)
        details = dict((publication.details or {}) if publication else {})
        if media_id and article.featured_image:
            details.setdefault("media", {})[str(article.featured_image.id)] = media_id
        return RemoteResult(str(post.get("id")), post.get("link"), "draft" if post.get("status") == "draft" else "published", details)

    def unpublish(self, article, publication, client) -> RemoteResult:
        if not publication or not publication.remote_id:
            raise PublishError("Article has no WordPress post to unpublish", retryable=False)
        post = self._call(client, "POST", f"/posts/{publication.remote_id}", json_body={"status": "draft"})
        return RemoteResult(str(post.get("id")), post.get("link"), "unpublished", publication.details or {})

    def status(self, article, publication, client) -> RemoteResult:
        post = self._call(client, "GET", f"/posts/{publication.remote_id}?context=edit")
        st = post.get("status")
        mapped = {"publish": "published", "draft": "draft", "future": "scheduled", "private": "private", "trash": "deleted"}.get(st, st or "unknown")
        if publication.remote_status == "unpublished" and mapped == "draft":
            mapped = "unpublished"
        return RemoteResult(str(post.get("id")), post.get("link"), mapped, publication.details or {})

    def check(self, client) -> str:
        me = self._call(client, "GET", "/users/me?context=edit")
        return f"Connected as {me.get('name') or me.get('slug')}"


class WebhookConnector:
    """POSTs signed JSON to a custom website. Signature: X-Newswire-Signature: sha256=<hex HMAC>."""

    def __init__(self, dest: PublishingDestination):
        if not dest.base_url:
            raise PublishError("Webhook destination needs a URL", retryable=False)
        self.dest = dest
        self.secret = decrypt_secret(dest.secret_encrypted) or ""

    def _send(self, client, event: str, payload: dict, delivery: str) -> dict:
        body = json.dumps({"event": event, "delivery_id": delivery, **payload}, default=str).encode()
        headers = {"Content-Type": "application/json", "X-Newswire-Event": event, "X-Newswire-Delivery": delivery}
        if self.secret:
            sig = hmac.new(self.secret.encode(), body, hashlib.sha256).hexdigest()
            headers["X-Newswire-Signature"] = f"sha256={sig}"
        resp = safe_request(client, "POST", self.dest.base_url, content=body, headers=headers, max_bytes=1_000_000)
        if resp.status_code >= 400:
            raise PublishError(f"Webhook returned HTTP {resp.status_code}", retryable=resp.status_code >= 500 or resp.status_code == 429)
        try:
            data = resp.json()
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}

    def publish(self, article, publication, mode, client) -> RemoteResult:
        event = "article.updated" if publication and publication.remote_status == "published" else "article.published"
        if mode == "draft":
            event = "article.draft"
        data = self._send(client, event, {"article": article_payload(article)}, f"{article.id}:{article.version}:{event}")
        return RemoteResult(str(data.get("id") or article.id), data.get("url"), "draft" if mode == "draft" else "published")

    def unpublish(self, article, publication, client) -> RemoteResult:
        data = self._send(client, "article.unpublished", {"article": {"id": str(article.id), "slug": article.slug}},
                          f"{article.id}:{article.version}:unpublish")
        return RemoteResult(str(data.get("id") or article.id), None, "unpublished")

    def status(self, article, publication, client) -> RemoteResult:
        return RemoteResult(publication.remote_id, publication.remote_url, publication.remote_status)

    def check(self, client) -> str:
        self._send(client, "ping", {}, "ping")
        return "Webhook endpoint accepted a ping"


def connector_for(dest: PublishingDestination):
    if dest.kind == "builtin":
        return BuiltinConnector(dest)
    if dest.kind == "wordpress":
        return WordPressConnector(dest)
    if dest.kind == "webhook":
        return WebhookConnector(dest)
    raise PublishError(f"Unknown destination kind {dest.kind}", retryable=False)


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Helper for receiving sites (documented in docs/PUBLISHING.md)."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


__all__ = ["connector_for", "PublishError", "RemoteResult", "article_payload", "verify_signature", "httpx"]
