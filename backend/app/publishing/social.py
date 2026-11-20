"""Facebook Page, Instagram account and YouTube channel publishing, and the OAuth connection.

Credentials (app secret, Page token, YouTube refresh token) are kept as one Fernet-encrypted
JSON object in `PublishingDestination.secret_encrypted`; non-secret details (App ID, Page and
channel names) are in `config`. Nothing secret is ever returned by the API or put in a URL
NEWSWIRE opens: Graph and YouTube calls send tokens in the Authorization header.

Facebook and Instagram use one Meta app. An administrator enters it once (Settings →
Integrations, stored in the `meta_app` system setting with the secret encrypted); after that a
destination only needs Connect. A destination may still carry its own App ID and secret, which
then take precedence (installations set up before the shared app existed keep working).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from typing import Any
import ipaddress
from urllib.parse import urlencode, urlsplit

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.db import utcnow
from app.core.http import ResponseTooLarge, SafeResponse, safe_request
from app.models import Article, ArticlePublication, PublishingDestination, SystemSetting
from app.publishing.connectors import PublishError, RemoteResult
from app.videos.service import VideoError, file_chunks, file_range, is_short, reel_problems, require_file

SOCIAL_KINDS = ("facebook", "instagram", "youtube")
META_KINDS = ("facebook", "instagram")
OAUTH_STATE_TTL = 15 * 60
CALLBACK_PATH = "/api/v1/publishing/oauth/callback"
META_APP_KEY = "meta_app"
INSTAGRAM_SCOPES = "instagram_basic,instagram_content_publish,pages_show_list,pages_read_engagement,business_management"


class RemoteGone(PublishError):
    """The post or video no longer exists on the platform (deleted there)."""

    def __init__(self, message: str = "It was deleted on the platform"):
        super().__init__(message, retryable=False)


class OAuthError(Exception):
    pass


# --- stored credentials ----------------------------------------------------------------------


def load_secrets(dest: PublishingDestination) -> dict:
    raw = decrypt_secret(dest.secret_encrypted)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def save_secrets(dest: PublishingDestination, data: dict) -> None:
    clean = {k: v for k, v in data.items() if v not in (None, "", [], {})}
    dest.secret_encrypted = encrypt_secret(json.dumps(clean)) if clean else None


def set_config(dest: PublishingDestination, **values) -> None:
    cfg = dict(dest.config or {})
    for k, v in values.items():
        if v is None:
            cfg.pop(k, None)
        else:
            cfg[k] = v
    dest.config = cfg  # a new dict, so SQLAlchemy sees the change


def is_connected(dest: PublishingDestination) -> bool:
    cfg, sec = dest.config or {}, load_secrets(dest)
    if dest.kind == "facebook":
        return bool(cfg.get("page_id") and sec.get("page_token"))
    if dest.kind == "instagram":
        return bool(cfg.get("ig_user_id") and sec.get("page_token"))
    if dest.kind == "youtube":
        return bool(sec.get("refresh_token"))
    return False


def clear_connection(dest: PublishingDestination) -> None:
    sec = load_secrets(dest)
    save_secrets(dest, {"client_secret": sec.get("client_secret")})
    set_config(dest, page_id=None, page_name=None, pages=None, channel_id=None, channel_title=None,
               ig_user_id=None, ig_username=None, connected_at=None, oauth=None)


# --- the shared Meta app (Facebook and Instagram) ---------------------------------------------------


def meta_app(db: Session) -> dict:
    """The Meta app entered once under Settings → Integrations. The secret is decrypted here only."""
    row = db.get(SystemSetting, META_APP_KEY)
    v = row.value if row is not None and isinstance(row.value, dict) else {}
    return {
        "client_id": v.get("app_id") or None,
        "client_secret": decrypt_secret(v.get("secret_encrypted")) if v.get("secret_encrypted") else None,
        "login_config_id": v.get("login_config_id") or None,
        "instagram_login_config_id": v.get("instagram_login_config_id") or None,
    }


def save_meta_app(db: Session, *, app_id: str, secret: str | None, login_config_id: str | None,
                  instagram_login_config_id: str | None, user_id=None) -> None:
    row = db.get(SystemSetting, META_APP_KEY)
    old = row.value if row is not None and isinstance(row.value, dict) else {}
    value = {
        "app_id": app_id,
        "secret_encrypted": encrypt_secret(secret) if secret else old.get("secret_encrypted"),
        "login_config_id": login_config_id or None,
        "instagram_login_config_id": instagram_login_config_id or None,
    }
    if row is None:
        db.add(SystemSetting(key=META_APP_KEY, value=value, updated_by_id=user_id))
    else:
        row.value = value
        row.updated_by_id = user_id
        row.updated_at = utcnow()


def uses_shared_app(dest: PublishingDestination) -> bool:
    return dest.kind in META_KINDS and not (dest.config or {}).get("client_id")


def app_credentials(db: Session, dest: PublishingDestination) -> dict:
    """The OAuth app a destination signs in with: its own, or the shared Meta app."""
    if uses_shared_app(dest):
        app = meta_app(db)
        if dest.kind == "instagram":
            app["login_config_id"] = app.get("instagram_login_config_id")
        return app
    cfg = dest.config or {}
    return {"client_id": cfg.get("client_id"), "client_secret": load_secrets(dest).get("client_secret"),
            "login_config_id": cfg.get("login_config_id")}


def redirect_uri() -> str:
    return get_settings().public_base_url.rstrip("/") + CALLBACK_PATH


# --- OAuth state: signed, short-lived, single use ----------------------------------------------


def _sign(payload: bytes) -> str:
    key = hashlib.sha256(("newswire-oauth:" + get_settings().secret_key).encode()).digest()
    return base64.urlsafe_b64encode(hmac.new(key, payload, hashlib.sha256).digest()).decode().rstrip("=")


def make_state(dest: PublishingDestination, user_id) -> str:
    nonce = secrets.token_urlsafe(16)
    expires = int(time.time()) + OAUTH_STATE_TTL
    set_config(dest, oauth={"nonce": hashlib.sha256(nonce.encode()).hexdigest(), "expires": expires,
                            "user_id": str(user_id) if user_id else None})
    payload = json.dumps({"d": str(dest.id), "n": nonce, "e": expires}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=") + "." + _sign(payload)


def read_state(db: Session, state: str | None) -> tuple[PublishingDestination, str | None]:
    """Return the destination the state was made for, and the user who started it. Single use."""
    expired = OAuthError("This connection link has expired or was already used. Click Connect again.")
    if not state or "." not in state:
        raise expired
    body, sig = state.rsplit(".", 1)
    try:
        payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        data = json.loads(payload)
        dest_id = uuid.UUID(str(data["d"]))
    except (ValueError, KeyError, TypeError) as exc:
        raise expired from exc
    if not hmac.compare_digest(_sign(payload), sig):
        raise expired
    dest = db.get(PublishingDestination, dest_id)
    if dest is None or dest.kind not in SOCIAL_KINDS:
        raise expired
    pending = (dest.config or {}).get("oauth") or {}
    nonce_ok = hmac.compare_digest(pending.get("nonce") or "", hashlib.sha256(str(data.get("n")).encode()).hexdigest())
    if not nonce_ok or int(data.get("e") or 0) < time.time() or int(pending.get("expires") or 0) < time.time():
        raise expired
    set_config(dest, oauth=None)
    return dest, pending.get("user_id")


def authorize_url(dest: PublishingDestination, state: str, app: dict) -> str:
    s = get_settings()
    client_id = app.get("client_id")
    if not client_id or not app.get("client_secret"):
        if uses_shared_app(dest):
            raise OAuthError("Set up the Meta app first: Settings → Integrations (one time, for Facebook and Instagram).")
        what = "App ID and App secret" if dest.kind in META_KINDS else "Client ID and Client secret"
        raise OAuthError(f"Enter the {what} first (edit the destination).")
    if dest.kind in META_KINDS:
        params = {"client_id": client_id, "redirect_uri": redirect_uri(), "state": state, "response_type": "code"}
        if app.get("login_config_id"):
            params["config_id"] = app["login_config_id"]  # Facebook Login for Business
        else:
            params["scope"] = INSTAGRAM_SCOPES if dest.kind == "instagram" else s.facebook_scopes
        return f"{s.facebook_dialog_url.rstrip('/')}/{s.facebook_graph_version}/dialog/oauth?{urlencode(params)}"
    params = {
        "client_id": client_id, "redirect_uri": redirect_uri(), "response_type": "code", "state": state,
        "scope": "https://www.googleapis.com/auth/youtube.force-ssl",
        # offline + consent: Google returns a refresh token every time, so the connection keeps working.
        "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true",
    }
    return f"{s.google_auth_url}?{urlencode(params)}"


def _json(resp: SafeResponse) -> dict:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def complete_oauth(dest: PublishingDestination, code: str, client: httpx.Client, app: dict) -> str:
    """Exchange the code, store the connection, and return a sentence describing it."""
    if dest.kind in META_KINDS:
        return _facebook_complete(dest, code, client, app)
    return _youtube_complete(dest, code, client)


def _graph_base() -> str:
    s = get_settings()
    return f"{s.facebook_graph_url.rstrip('/')}/{s.facebook_graph_version}"


def _facebook_complete(dest: PublishingDestination, code: str, client: httpx.Client, app: dict) -> str:
    sec = load_secrets(dest)
    base = _graph_base()
    common = {"client_id": app.get("client_id"), "client_secret": app.get("client_secret")}
    resp = safe_request(client, "POST", f"{base}/oauth/access_token",
                        data={**common, "redirect_uri": redirect_uri(), "code": code}, headers={"Accept": "application/json"})
    short = _json(resp).get("access_token")
    if resp.status_code >= 400 or not short:
        raise OAuthError(f"Facebook did not accept the sign-in: {_fb_message(resp)}. Check the App ID, the App "
                         f"secret, and that {redirect_uri()} is listed as a Valid OAuth Redirect URI.")
    resp = safe_request(client, "POST", f"{base}/oauth/access_token",
                        data={**common, "grant_type": "fb_exchange_token", "fb_exchange_token": short},
                        headers={"Accept": "application/json"})
    # Page tokens from a long-lived user token do not expire; fall back to the short one if needed.
    user_token = _json(resp).get("access_token") or short
    fields = "id,name,access_token,tasks"
    if dest.kind == "instagram":
        fields += ",instagram_business_account{id,username}"
    resp = safe_request(client, "GET", f"{base}/me/accounts?fields={fields}&limit=100",
                        headers={"Authorization": f"Bearer {user_token}", "Accept": "application/json"})
    if resp.status_code >= 400:
        raise OAuthError(f"Could not list your Facebook Pages: {_fb_message(resp)}")
    listed = [p for p in _json(resp).get("data") or [] if isinstance(p, dict) and p.get("id") and p.get("access_token")]
    pages = [p for p in listed if not p.get("tasks") or {"CREATE_CONTENT", "MANAGE"} & set(p.get("tasks") or [])]
    if not pages:
        if listed:
            raise OAuthError("Your role on these Facebook Pages does not allow creating posts. Ask a Page admin "
                             "for full control or content access, then connect again.")
        raise OAuthError("Facebook did not give Samyabadi access to any Page. Connect again and, when Facebook "
                         "asks, select the Page and allow every permission.")
    if dest.kind == "instagram":
        choices = []
        for p in pages:
            ig = p.get("instagram_business_account")
            if isinstance(ig, dict) and ig.get("id"):
                handle = str(ig.get("username") or ig["id"])
                choices.append({"id": str(ig["id"]), "name": f"@{handle}", "username": handle, "token": p["access_token"],
                                "page_id": str(p["id"]), "page_name": str(p.get("name") or p["id"])})
        if not choices:
            raise OAuthError("None of your Facebook Pages has an Instagram account linked. In the Instagram app, switch "
                             "the account to Professional (Business or Creator) and link it to your Facebook Page "
                             "(Settings → Account center), then connect again.")
        what, noun = "Instagram account", "accounts"
    else:
        choices = [{"id": str(p["id"]), "name": str(p.get("name") or p["id"]), "token": p["access_token"]} for p in pages]
        what, noun = "Facebook Page", "Pages"
    if len(choices) == 1:
        _select_page(dest, choices[0])
        return f"Connected to {what} “{choices[0]['name']}”"
    save_secrets(dest, {"client_secret": sec.get("client_secret"), "pending_pages": choices})
    set_config(dest, page_id=None, page_name=None, ig_user_id=None, ig_username=None,
               pages=[{"id": c["id"], "name": c["name"] + (f" ({c['page_name']})" if c.get("page_name") else "")}
                      for c in choices])
    return f"Signed in to Facebook; choose which of your {len(choices)} {noun} to publish to"


def _select_page(dest: PublishingDestination, page: dict) -> None:
    sec = load_secrets(dest)
    save_secrets(dest, {"client_secret": sec.get("client_secret"), "page_token": page["token"]})
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if dest.kind == "instagram":
        set_config(dest, ig_user_id=page["id"], ig_username=page.get("username"), page_id=page.get("page_id"),
                   page_name=page.get("page_name"), pages=None, connected_at=now)
    else:
        set_config(dest, page_id=page["id"], page_name=page["name"], pages=None, connected_at=now)


def choose_page(dest: PublishingDestination, page_id: str) -> str:
    pending = load_secrets(dest).get("pending_pages") or []
    page = next((p for p in pending if p.get("id") == page_id), None)
    what = "Instagram account" if dest.kind == "instagram" else "Facebook Page"
    if page is None:
        raise OAuthError(f"That {what} is not in the list Facebook returned. Click Connect again.")
    _select_page(dest, page)
    return f"Connected to {what} “{page['name']}”"


def _youtube_complete(dest: PublishingDestination, code: str, client: httpx.Client) -> str:
    s = get_settings()
    cfg, sec = dest.config or {}, load_secrets(dest)
    resp = safe_request(client, "POST", s.google_token_url, headers={"Accept": "application/json"}, data={
        "code": code, "client_id": cfg.get("client_id"), "client_secret": sec.get("client_secret"),
        "redirect_uri": redirect_uri(), "grant_type": "authorization_code",
    })
    data = _json(resp)
    if resp.status_code >= 400 or not data.get("access_token"):
        raise OAuthError(f"Google did not accept the sign-in: {data.get('error_description') or data.get('error') or resp.status_code}. "
                         f"Check the Client ID, the Client secret, and that {redirect_uri()} is an Authorized redirect URI.")
    refresh = data.get("refresh_token")
    if not refresh:
        raise OAuthError("Google did not return a lasting connection. Remove Samyabadi at "
                         "https://myaccount.google.com/permissions and connect again.")
    resp = safe_request(client, "GET", f"{s.youtube_api_url}/channels?part=snippet&mine=true",
                        headers={"Authorization": f"Bearer {data['access_token']}", "Accept": "application/json"})
    if resp.status_code >= 400:
        raise OAuthError(f"Could not read your YouTube channel: {_yt_message(resp)}")
    items = _json(resp).get("items") or []
    if not items:
        raise OAuthError("This Google account has no YouTube channel. Create one at youtube.com (or pick the "
                         "account that owns the channel), then connect again.")
    channel = items[0]
    title = (channel.get("snippet") or {}).get("title") or channel.get("id")
    save_secrets(dest, {"client_secret": sec.get("client_secret"), "refresh_token": refresh})
    set_config(dest, channel_id=channel.get("id"), channel_title=title,
               connected_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    return f"Connected to YouTube channel “{title}”"


# --- shared helpers --------------------------------------------------------------------------------


def social_text(article: Article, options: dict, link: str | None, *, with_link: bool) -> str:
    text = (options.get("message") or "").strip()
    if not text:
        parts = [article.headline.strip()]
        summary = (article.excerpt or article.seo_description or "").strip()
        if summary and summary != parts[0]:
            parts.append(summary)
        text = "\n\n".join(parts)
    if with_link and link and link not in text:
        text = f"{text}\n\n{link}"
    return text


def _video_file(article: Article):
    if article.video is None:
        raise PublishError("This needs a video. Upload one in the article's Video box.", retryable=False)
    try:
        return require_file(article.video)
    except VideoError as exc:
        raise PublishError(str(exc), retryable=False) from exc


# --- Facebook ------------------------------------------------------------------------------------------

FB_RATE_LIMIT = {4, 17, 32, 613}
FB_TOKEN_SUBCODES = {458, 459, 460, 463, 464, 467, 492}


def _fb_message(resp: SafeResponse) -> str:
    body = _json(resp)
    err = body.get("error") or {}
    debug = body.get("debug_info")  # the video upload host's error shape
    if not err and isinstance(debug, dict):
        return str(debug.get("message") or debug.get("type") or f"HTTP {resp.status_code}")[:300]
    if isinstance(err, dict):
        return str(err.get("error_user_msg") or err.get("message") or f"HTTP {resp.status_code}")[:300]
    return f"HTTP {resp.status_code}"


def _fb_error(resp: SafeResponse) -> PublishError:
    err = _json(resp).get("error") or {}
    err = err if isinstance(err, dict) else {}
    code, sub = err.get("code"), err.get("error_subcode")
    msg = _fb_message(resp)
    if code == 190 or sub in FB_TOKEN_SUBCODES:
        return PublishError("The Facebook connection has expired or was removed. Open Publishing → Destinations "
                            f"and connect the Page again. ({msg})", retryable=False)
    if code == 100 and sub == 33 or resp.status_code == 404:
        return RemoteGone(f"Facebook says it no longer exists ({msg})")
    if code in FB_RATE_LIMIT or (isinstance(code, int) and 80001 <= code <= 80014):
        return PublishError(f"Facebook's rate limit was reached; Samyabadi will try again later. ({msg})")
    if code in (10, 3) or (isinstance(code, int) and 200 <= code <= 299):
        return PublishError(f"Facebook did not allow this: {msg}. Connect the Page again and allow Samyabadi to "
                            "create content on it.", retryable=False)
    if code == 368:
        return PublishError(f"Facebook blocked the post: {msg}", retryable=False)
    debug = _json(resp).get("debug_info")
    transient_upload = isinstance(debug, dict) and debug.get("retriable") is True
    if code in (1, 2) or resp.status_code >= 500 or err.get("is_transient") or transient_upload:
        return PublishError(f"Facebook is temporarily unavailable; Samyabadi will try again. ({msg})")
    return PublishError(f"Facebook refused it: {msg}", retryable=False)


def _absolute_fb(url: str | None) -> str | None:
    if not url:
        return None
    return "https://www.facebook.com" + url if url.startswith("/") else url


class FacebookConnector:
    supports_draft = False

    def __init__(self, dest: PublishingDestination):
        cfg, sec = dest.config or {}, load_secrets(dest)
        self.page_id = cfg.get("page_id")
        self.token = sec.get("page_token")
        if not self.page_id or not self.token:
            raise PublishError(f"{dest.name} is not connected to a Facebook Page yet. Open Publishing → "
                               "Destinations and click Connect.", retryable=False)
        self.dest = dest
        self.graph = _graph_base()
        s = get_settings()
        self.rupload = f"{s.facebook_upload_url.rstrip('/')}/video-upload/{s.facebook_graph_version}"

    def _call(self, client, method: str, url: str, *, data=None, files=None, content=None, headers=None,
              auth: str = "Bearer") -> dict:
        if not url.startswith("http"):
            url = self.graph + url
        h = {"Authorization": f"{auth} {self.token}", "Accept": "application/json", **(headers or {})}
        kwargs: dict[str, Any] = {}
        if data is not None:
            kwargs["data"] = data
        if files is not None:
            kwargs["files"] = files
        if content is not None:
            kwargs["content"] = content
        try:
            resp = safe_request(client, method, url, headers=h, max_bytes=2_000_000, **kwargs)
        except ResponseTooLarge as exc:
            raise PublishError("Facebook sent an unexpectedly large response") from exc
        body = _json(resp)
        if resp.status_code >= 400 or body.get("error"):
            raise _fb_error(resp)
        return body

    def _permalink(self, client, object_id: str) -> str | None:
        try:
            return _absolute_fb(self._call(client, "GET", f"/{object_id}?fields=permalink_url").get("permalink_url"))
        except PublishError:
            return None

    def publish(self, article, publication, mode, client, options=None) -> RemoteResult:
        if mode == "draft":
            raise PublishError("Facebook has no drafts. Publish now or schedule it.", retryable=False)
        options = options or {}
        fmt = options.get("format") or "post"
        link = options.get("link")
        details = dict((publication.details or {}) if publication else {})
        live = bool(publication and publication.remote_id and publication.remote_status in ("published", "processing"))
        if live and details.get("format") == fmt:
            try:
                if fmt == "post":
                    self._call(client, "POST", f"/{publication.remote_id}",
                               data={"message": social_text(article, options, link, with_link=not details.get("link"))})
                    return RemoteResult(publication.remote_id, publication.remote_url, "published", details)
                if details.get("video_asset_id") == str(article.video_id):
                    self._call(client, "POST", f"/{publication.remote_id}",
                               data={"description": social_text(article, options, link, with_link=True)})
                    return RemoteResult(publication.remote_id, publication.remote_url, publication.remote_status, details)
            except RemoteGone:
                pass  # deleted on Facebook: post it again
        return self._reel(article, options, link, client) if fmt == "reel" else self._post(article, options, link, client)

    def _post(self, article, options, link, client) -> RemoteResult:
        img = article.featured_image
        img_path = get_settings().images_dir / img.local_path if img is not None and img.local_path else None
        if link:
            # A link post shows the website's preview card (its title and image) and sends readers there.
            res = self._call(client, "POST", f"/{self.page_id}/feed",
                             data={"message": social_text(article, options, link, with_link=False), "link": link})
            post_id = str(res.get("id"))
        elif img_path is not None and img_path.is_file():
            caption = social_text(article, options, None, with_link=False)
            res = self._call(client, "POST", f"/{self.page_id}/photos", data={"caption": caption},
                             files={"source": (img_path.name, img_path.read_bytes(), img.mime or "image/jpeg")})
            post_id = str(res.get("post_id") or res.get("id"))
        else:
            res = self._call(client, "POST", f"/{self.page_id}/feed",
                             data={"message": social_text(article, options, None, with_link=False)})
            post_id = str(res.get("id"))
        if not post_id or post_id == "None":
            raise PublishError("Facebook did not return the new post's id", retryable=False)
        url = self._permalink(client, post_id) or f"https://www.facebook.com/{post_id}"
        return RemoteResult(post_id, url, "published", {"format": "post", "link": link})

    def _reel(self, article, options, link, client) -> RemoteResult:
        problems = reel_problems(article.video)
        if problems:
            raise PublishError(" ".join(problems), retryable=False)
        path = _video_file(article)
        size = path.stat().st_size
        start = self._call(client, "POST", f"/{self.page_id}/video_reels", data={"upload_phase": "start"})
        video_id = str(start.get("video_id") or "")
        if not video_id:
            raise PublishError("Facebook did not start the Reel upload", retryable=True)
        uploaded = self._call(
            client, "POST", f"{self.rupload}/{video_id}", content=file_chunks(path), auth="OAuth",
            headers={"offset": "0", "file_size": str(size), "Content-Length": str(size),
                     "Content-Type": "application/octet-stream"},
        )
        if uploaded.get("success") is False:
            raise PublishError("Facebook did not accept the Reel's video file")
        self._call(client, "POST", f"/{self.page_id}/video_reels", data={
            "upload_phase": "finish", "video_id": video_id, "video_state": "PUBLISHED",
            "description": social_text(article, options, link, with_link=True),
        })
        url = self._permalink(client, video_id) or f"https://www.facebook.com/reel/{video_id}"
        # Facebook processes the video before the Reel appears; the publishing queue checks on it.
        return RemoteResult(video_id, url, "processing",
                            {"format": "reel", "video_asset_id": str(article.video_id), "link": link})

    def unpublish(self, article, publication, client) -> RemoteResult:
        if not publication or not publication.remote_id:
            raise PublishError("Nothing was posted to this Facebook Page", retryable=False)
        try:
            self._call(client, "DELETE", f"/{publication.remote_id}")
        except RemoteGone:
            pass
        return RemoteResult(publication.remote_id, None, "unpublished", publication.details or {})

    def status(self, article, publication, client) -> RemoteResult:
        details = dict(publication.details or {})
        try:
            if details.get("format") == "reel":
                data = self._call(client, "GET", f"/{publication.remote_id}?fields=status,permalink_url")
                st = data.get("status") or {}
                phases = [st.get(k) or {} for k in ("uploading_phase", "processing_phase", "publishing_phase")]
                failed = st.get("video_status") == "error" or any(p.get("status") == "error" for p in phases)
                if failed:
                    errors = [e.get("message") for p in phases for e in (p.get("errors") or []) if isinstance(e, dict)]
                    details["error"] = "; ".join(filter(None, errors))[:300] or "Facebook could not process the video"
                    mapped = "failed"
                elif (st.get("publishing_phase") or {}).get("status") == "complete" or st.get("video_status") == "ready":
                    mapped = "published"
                else:
                    mapped = "processing"
                url = _absolute_fb(data.get("permalink_url")) or publication.remote_url
            else:
                data = self._call(client, "GET", f"/{publication.remote_id}?fields=id,permalink_url,is_published")
                mapped = "published" if data.get("is_published", True) else "draft"
                url = _absolute_fb(data.get("permalink_url")) or publication.remote_url
        except RemoteGone:
            return RemoteResult(publication.remote_id, None,
                                "unpublished" if publication.remote_status == "unpublished" else "deleted", details)
        return RemoteResult(publication.remote_id, url, mapped, details)

    def check(self, client) -> str:
        page = self._call(client, "GET", f"/{self.page_id}?fields=id,name")
        return f"Connected to Facebook Page “{page.get('name') or self.page_id}”"


# --- Instagram -----------------------------------------------------------------------------------------
#
# Instagram (Graph API for professional accounts linked to a Facebook Page) does not take file
# uploads: it downloads the photo or video from a public address. A NEWSWIRE that is reachable
# from the internet (NEWSWIRE_PUBLIC_BASE_URL on https) serves its own /media files; otherwise
# the picture's original public address (Openverse, Wikimedia) is used when there is one.

IG_PUBLIC_HINT = ("Instagram downloads the picture from the internet, and this Samyabadi is only reachable on this "
                  "computer. Use an image from the Image Library (Openverse or Wikimedia), or run Samyabadi on a "
                  "public https address (NEWSWIRE_PUBLIC_BASE_URL).")


def public_base() -> str | None:
    """NEWSWIRE's own address when the internet can reach it, else None."""
    base = get_settings().public_base_url.rstrip("/")
    parts = urlsplit(base)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or host == "localhost" or host.endswith((".localhost", ".local", ".test")):
        return None
    try:
        if not ipaddress.ip_address(host).is_global:
            return None
    except ValueError:
        pass  # a name, not an address
    return base


def public_image_url(article: Article) -> str | None:
    img = article.featured_image
    if img is None:
        return None
    base = public_base()
    if base and img.local_path:
        return f"{base}/media/{img.local_path}"
    if img.original_url and img.original_url.startswith("https://") and img.provider != "upload":
        return img.original_url
    return None


def public_video_url(article: Article) -> str | None:
    base = public_base()
    return f"{base}/media/{article.video.local_path}" if base and article.video is not None else None


def _ig_error(resp: SafeResponse) -> PublishError:
    """Facebook's error mapping (same Graph API), worded for Instagram."""
    err = _fb_error(resp)
    msg = str(err).replace("Facebook", "Instagram").replace("the Page", "the account")
    return RemoteGone(msg) if isinstance(err, RemoteGone) else PublishError(msg, retryable=err.retryable)


class InstagramConnector:
    supports_draft = False

    def __init__(self, dest: PublishingDestination):
        cfg, sec = dest.config or {}, load_secrets(dest)
        self.ig_id = cfg.get("ig_user_id")
        self.token = sec.get("page_token")
        if not self.ig_id or not self.token:
            raise PublishError(f"{dest.name} is not connected to an Instagram account yet. Open Publishing → "
                               "Destinations and click Connect.", retryable=False)
        self.dest = dest
        self.graph = _graph_base()

    def _call(self, client, method: str, path: str, *, data=None) -> dict:
        url = path if path.startswith("http") else self.graph + path
        try:
            resp = safe_request(client, method, url, data=data, max_bytes=2_000_000,
                                headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"})
        except ResponseTooLarge as exc:
            raise PublishError("Instagram sent an unexpectedly large response") from exc
        body = _json(resp)
        if resp.status_code >= 400 or body.get("error"):
            raise _ig_error(resp)
        return body

    def _publish_container(self, client, creation_id: str) -> tuple[str, str | None]:
        res = self._call(client, "POST", f"/{self.ig_id}/media_publish", data={"creation_id": creation_id})
        media_id = str(res.get("id") or "")
        if not media_id:
            raise PublishError("Instagram did not return the new post's id")
        try:
            link = self._call(client, "GET", f"/{media_id}?fields=permalink").get("permalink")
        except PublishError:
            link = None
        return media_id, link

    def publish(self, article, publication, mode, client, options=None) -> RemoteResult:
        if mode == "draft":
            raise PublishError("Instagram has no drafts. Publish now or schedule it.", retryable=False)
        options = options or {}
        fmt = options.get("format") or "post"
        live = bool(publication and publication.remote_id and publication.remote_status in ("published", "processing"))
        if live:
            # Instagram cannot edit a post's photo or caption through the API.
            details = dict(publication.details or {})
            details["warning"] = "Instagram posts cannot be edited by apps; the original post stays as it was."
            return RemoteResult(publication.remote_id, publication.remote_url, publication.remote_status, details)
        caption = social_text(article, options, options.get("link"), with_link=True)[:2200]
        if fmt == "reel":
            problems = reel_problems(article.video)
            if problems:
                raise PublishError(" ".join(problems), retryable=False)
            video_url = public_video_url(article)
            if not video_url:
                raise PublishError("Instagram downloads the video from the internet, and this Samyabadi is only "
                                   "reachable on this computer. Run Samyabadi on a public https address "
                                   "(NEWSWIRE_PUBLIC_BASE_URL) to post Reels.", retryable=False)
            res = self._call(client, "POST", f"/{self.ig_id}/media",
                             data={"media_type": "REELS", "video_url": video_url, "caption": caption})
            container = str(res.get("id") or "")
            if not container:
                raise PublishError("Instagram did not accept the Reel")
            # Instagram fetches and processes the video first; the publishing queue finishes the post.
            return RemoteResult(container, None, "processing", {"format": "reel", "container_id": container})
        image_url = public_image_url(article)
        if not image_url:
            raise PublishError(IG_PUBLIC_HINT if article.featured_image else
                               "Instagram needs a picture. Add a featured image to the article.", retryable=False)
        res = self._call(client, "POST", f"/{self.ig_id}/media", data={"image_url": image_url, "caption": caption})
        container = str(res.get("id") or "")
        if not container:
            raise PublishError("Instagram did not accept the picture")
        media_id, link = self._publish_container(client, container)
        return RemoteResult(media_id, link, "published", {"format": "post"})

    def unpublish(self, article, publication, client) -> RemoteResult:
        raise PublishError("Instagram does not let apps delete posts. Delete it in the Instagram app "
                           "(… on the post → Delete).", retryable=False)

    def status(self, article, publication, client) -> RemoteResult:
        details = dict(publication.details or {})
        try:
            container = details.get("container_id")
            if publication.remote_status == "processing" and container:
                st = self._call(client, "GET", f"/{container}?fields=status_code,status").get("status_code")
                if st == "FINISHED":
                    media_id, link = self._publish_container(client, container)
                    details.pop("container_id", None)
                    return RemoteResult(media_id, link, "published", details)
                if st in ("ERROR", "EXPIRED"):
                    details["error"] = "Instagram could not process the video (it must be MP4/H.264, 3–90 s, vertical)"
                    return RemoteResult(publication.remote_id, None, "failed", details)
                return RemoteResult(publication.remote_id, None, "processing", details)
            data = self._call(client, "GET", f"/{publication.remote_id}?fields=id,permalink")
            return RemoteResult(publication.remote_id, data.get("permalink") or publication.remote_url, "published", details)
        except RemoteGone:
            return RemoteResult(publication.remote_id, None, "deleted", details)

    def check(self, client) -> str:
        me = self._call(client, "GET", f"/{self.ig_id}?fields=id,username")
        return f"Connected to Instagram account @{me.get('username') or self.ig_id}"


# --- YouTube ------------------------------------------------------------------------------------------

YT_CATEGORY_NEWS = "25"  # News & Politics


def _yt_reason(resp: SafeResponse) -> tuple[str, str]:
    err = _json(resp).get("error")
    if isinstance(err, dict):
        reasons = [e.get("reason") for e in err.get("errors") or [] if isinstance(e, dict)]
        return (reasons[0] if reasons else "") or "", str(err.get("message") or f"HTTP {resp.status_code}")[:300]
    if isinstance(err, str):  # OAuth token endpoint shape
        return err, str(_json(resp).get("error_description") or err)[:300]
    return "", f"HTTP {resp.status_code}"


def _yt_message(resp: SafeResponse) -> str:
    return _yt_reason(resp)[1]


def _yt_error(resp: SafeResponse) -> PublishError:
    reason, msg = _yt_reason(resp)
    if reason in ("quotaExceeded", "dailyLimitExceeded"):
        return PublishError("YouTube's daily API quota for this Google Cloud project is used up. It resets at midnight "
                            "Pacific time; retry the job after that.", retryable=False)
    if reason == "uploadLimitExceeded":
        return PublishError("This YouTube channel has reached its upload limit for now. Retry later.", retryable=False)
    if reason in ("rateLimitExceeded", "userRateLimitExceeded") or resp.status_code == 429:
        return PublishError(f"YouTube's rate limit was reached; Samyabadi will try again later. ({msg})")
    if resp.status_code == 401:
        return PublishError("The YouTube connection is no longer valid. Connect the channel again.", retryable=False)
    if reason == "youtubeSignupRequired":
        return PublishError("This Google account has no YouTube channel. Create one, then connect again.", retryable=False)
    if reason in ("videoNotFound",) or resp.status_code == 404:
        return RemoteGone(f"YouTube says the video no longer exists ({msg})")
    if resp.status_code >= 500:
        return PublishError(f"YouTube is temporarily unavailable; Samyabadi will try again. ({msg})")
    if resp.status_code == 403:
        return PublishError(f"YouTube did not allow this: {msg}", retryable=False)
    return PublishError(f"YouTube refused it: {msg}", retryable=False)


def _yt_clean(text: str) -> str:
    # YouTube rejects titles and descriptions that contain angle brackets.
    return text.replace("<", "‹").replace(">", "›").strip()


def _truncate_bytes(text: str, limit: int) -> str:
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    return data[: limit - 3].decode("utf-8", errors="ignore").rstrip() + "…"


class YouTubeConnector:
    supports_draft = True

    def __init__(self, dest: PublishingDestination):
        cfg, sec = dest.config or {}, load_secrets(dest)
        self.client_id = cfg.get("client_id")
        self.client_secret = sec.get("client_secret")
        self.refresh_token = sec.get("refresh_token")
        if not self.refresh_token:
            raise PublishError(f"{dest.name} is not connected to a YouTube channel yet. Open Publishing → "
                               "Destinations and click Connect.", retryable=False)
        self.dest = dest
        s = get_settings()
        self.api = s.youtube_api_url.rstrip("/")
        self.upload_api = s.youtube_upload_url.rstrip("/")
        self.token_url = s.google_token_url
        self._access: str | None = None

    def _token(self, client) -> str:
        if self._access:
            return self._access
        resp = safe_request(client, "POST", self.token_url, headers={"Accept": "application/json"}, data={
            "client_id": self.client_id, "client_secret": self.client_secret,
            "refresh_token": self.refresh_token, "grant_type": "refresh_token",
        })
        data = _json(resp)
        if resp.status_code >= 400 or not data.get("access_token"):
            reason, msg = _yt_reason(resp)
            if reason == "invalid_grant":
                raise PublishError("The YouTube connection has expired or was revoked. Connect the channel again. "
                                   "(Google projects in Testing mode end connections after 7 days; see "
                                   "docs/PUBLISHING.md.)", retryable=False)
            if reason in ("invalid_client", "unauthorized_client"):
                raise PublishError("Google rejected the Client ID or Client secret. Edit the destination and check "
                                   "them.", retryable=False)
            raise PublishError(f"Google sign-in failed: {msg}", retryable=resp.status_code >= 500)
        self._access = data["access_token"]
        return self._access

    def _call(self, client, method: str, url: str, *, json_body=None, content=None, headers=None,
              follow_redirects: bool = True) -> SafeResponse:
        h = {"Authorization": f"Bearer {self._token(client)}", "Accept": "application/json", **(headers or {})}
        kwargs: dict[str, Any] = {}
        if json_body is not None:
            kwargs["content"] = json.dumps(json_body).encode()
            h["Content-Type"] = "application/json; charset=UTF-8"
        elif content is not None:
            kwargs["content"] = content
        return safe_request(client, method, url, headers=h, max_bytes=2_000_000,
                            follow_redirects=follow_redirects, **kwargs)

    def _ok(self, resp: SafeResponse) -> dict:
        if resp.status_code >= 400:
            raise _yt_error(resp)
        return _json(resp)

    def _metadata(self, article, options, link, privacy: str) -> dict:
        title = _yt_clean(article.headline) or "Untitled"
        if len(title) > 100:  # YouTube counts characters here, not bytes
            title = title[:99].rstrip() + "…"
        description = (options.get("message") or "").strip() or (article.excerpt or article.seo_description or "").strip()
        if link and link not in description:
            description = f"{description}\n\n{link}".strip()
        tags, total = [], 0
        for t in article.tags:
            name = _yt_clean(t.name)
            cost = len(name) + (2 if " " in name else 0) + 1
            if name and total + cost <= 450:
                tags.append(name)
                total += cost
        snippet = {"title": title, "description": _truncate_bytes(_yt_clean(description), 4900),
                   "categoryId": YT_CATEGORY_NEWS}
        if tags:
            snippet["tags"] = tags
        return {"snippet": snippet, "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}

    def _watch_url(self, article, video_id: str) -> str:
        if article.video is not None and is_short(article.video):
            return f"https://www.youtube.com/shorts/{video_id}"
        return f"https://www.youtube.com/watch?v={video_id}"

    def _result(self, article, video_id: str, requested: str, actual: str | None, mode: str, details: dict,
                upload_status: str | None = None) -> RemoteResult:
        actual = actual or requested
        details = {**details, "privacy": actual}
        details.pop("warning", None)
        if actual != requested:
            details["warning"] = (f"YouTube made this video {actual} instead of {requested}. Videos uploaded through a "
                                  "Google Cloud project that has not passed YouTube's API audit stay private; see "
                                  "docs/PUBLISHING.md.")
        if mode == "draft":
            status = "draft"
        elif actual == "private":
            status = "private"
        elif upload_status == "uploaded":
            status = "processing"
        else:
            status = "published"
        return RemoteResult(video_id, self._watch_url(article, video_id), status, details)

    def publish(self, article, publication, mode, client, options=None) -> RemoteResult:
        options = options or {}
        privacy = "private" if mode == "draft" else (options.get("privacy") or "public")
        link = options.get("link")
        details = dict((publication.details or {}) if publication else {})
        meta = self._metadata(article, options, link, privacy)
        same_video = details.get("video_asset_id") == str(article.video_id)
        if publication and publication.remote_id and same_video and publication.remote_status not in ("deleted", "failed"):
            try:
                body = self._ok(self._call(client, "PUT", f"{self.api}/videos?part=snippet,status",
                                           json_body={"id": publication.remote_id, **meta}))
                actual = (body.get("status") or {}).get("privacyStatus")
                return self._result(article, publication.remote_id, privacy, actual, mode, details,
                                    (body.get("status") or {}).get("uploadStatus"))
            except RemoteGone:
                pass  # deleted on YouTube: upload it again
        path = _video_file(article)
        video = article.video
        size = path.stat().st_size
        start = self._call(client, "POST", f"{self.upload_api}/videos?uploadType=resumable&part=snippet,status",
                           json_body=meta, headers={"X-Upload-Content-Length": str(size),
                                                    "X-Upload-Content-Type": video.mime})
        if start.status_code >= 400:
            raise _yt_error(start)
        session = start.headers.get("location")
        if not session:
            raise PublishError("YouTube did not start the upload (no upload address returned)")
        body = self._upload(client, session, path, size, video.mime)
        video_id = str(body.get("id") or "")
        if not video_id:
            raise PublishError("YouTube did not return the new video's id", retryable=False)
        status = body.get("status") or {}
        details = {"video_asset_id": str(article.video_id)}
        thumb_note = self._thumbnail(article, video_id, client)
        if thumb_note:
            details["thumbnail"] = thumb_note
        return self._result(article, video_id, privacy, status.get("privacyStatus"), mode, details,
                            status.get("uploadStatus"))

    def _upload(self, client, session: str, path, size: int, mime: str) -> dict:
        """Send the file to the resumable session; after an interruption, ask how much arrived and resume."""
        offset = 0
        for _ in range(4):
            headers = {"Content-Type": mime, "Content-Length": str(size - offset)}
            if offset:
                headers["Content-Range"] = f"bytes {offset}-{size - 1}/{size}"
            resp = None
            try:
                resp = self._call(client, "PUT", session, content=file_range(path, offset), headers=headers,
                                  follow_redirects=False)
            except httpx.TransportError:
                resp = None
            if resp is not None and resp.status_code in (200, 201):
                return _json(resp)
            if resp is not None and resp.status_code not in (308,) and resp.status_code < 500:
                raise _yt_error(resp)
            probe = self._call(client, "PUT", session, content=b"",
                               headers={"Content-Range": f"bytes */{size}", "Content-Length": "0"},
                               follow_redirects=False)
            if probe.status_code in (200, 201):
                return _json(probe)
            if probe.status_code == 308:
                rng = probe.headers.get("range") or ""
                try:
                    offset = int(rng.rsplit("-", 1)[1]) + 1 if rng else 0
                except ValueError:
                    offset = 0
                continue
            if probe.status_code in (404, 410):
                raise PublishError("The YouTube upload session expired; Samyabadi will start the upload again.")
            raise _yt_error(probe)
        raise PublishError("The upload to YouTube kept being interrupted; Samyabadi will try again later.")

    def _thumbnail(self, article, video_id: str, client) -> str | None:
        """Best effort: custom thumbnails need a verified channel, a JPEG/PNG and at most 2 MB."""
        img = article.featured_image
        if img is None or not img.local_path or (img.mime or "") not in ("image/jpeg", "image/png"):
            return None
        path = get_settings().images_dir / img.local_path
        if not path.is_file() or path.stat().st_size > 2_000_000:
            return "The featured image was not used as the thumbnail (it must be a JPEG or PNG under 2 MB)."
        try:
            resp = self._call(client, "POST", f"{self.upload_api}/thumbnails/set?videoId={video_id}",
                              content=path.read_bytes(), headers={"Content-Type": img.mime})
        except httpx.HTTPError:
            return "The thumbnail could not be set."
        if resp.status_code >= 400:
            return f"YouTube did not accept the featured image as thumbnail: {_yt_message(resp)}"
        return None

    def _set_privacy(self, client, video_id: str, privacy: str) -> dict:
        return self._ok(self._call(client, "PUT", f"{self.api}/videos?part=status", json_body={
            "id": video_id, "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}))

    def unpublish(self, article, publication, client) -> RemoteResult:
        if not publication or not publication.remote_id:
            raise PublishError("Nothing was uploaded to this YouTube channel", retryable=False)
        try:
            self._set_privacy(client, publication.remote_id, "private")
        except RemoteGone:
            pass
        return RemoteResult(publication.remote_id, publication.remote_url, "unpublished",
                            {**(publication.details or {}), "privacy": "private"})

    def status(self, article, publication, client) -> RemoteResult:
        body = self._ok(self._call(client, "GET", f"{self.api}/videos?part=status&id={publication.remote_id}"))
        items = body.get("items") or []
        details = dict(publication.details or {})
        if not items:
            return RemoteResult(publication.remote_id, None, "deleted", details)
        st = items[0].get("status") or {}
        upload = st.get("uploadStatus")
        privacy = st.get("privacyStatus")
        details["privacy"] = privacy
        if upload in ("failed", "rejected", "deleted"):
            details["error"] = st.get("failureReason") or st.get("rejectionReason") or upload
            mapped = "failed" if upload != "deleted" else "deleted"
        elif privacy == "private":
            mapped = publication.remote_status if publication.remote_status in ("draft", "unpublished") else "private"
        elif upload == "uploaded":
            mapped = "processing"
        else:
            mapped = "published"
        return RemoteResult(publication.remote_id, publication.remote_url, mapped, details)

    def check(self, client) -> str:
        body = self._ok(self._call(client, "GET", f"{self.api}/channels?part=snippet&mine=true"))
        items = body.get("items") or []
        if not items:
            raise PublishError("This Google account has no YouTube channel", retryable=False)
        return f"Connected to YouTube channel “{(items[0].get('snippet') or {}).get('title') or items[0].get('id')}”"


def validate_options(dest: PublishingDestination, article: Article, options: dict | None, mode: str) -> dict:
    """Normalise one destination's options, or explain why the article cannot go there (ValueError)."""
    options = dict(options or {})
    message = (options.get("message") or "").strip()[:5000] or None
    if dest.kind == "facebook":
        if not is_connected(dest):
            raise ValueError("Not connected yet. Open Publishing → Destinations and click Connect.")
        if mode == "draft":
            raise ValueError("Facebook has no drafts. Choose Publish now or Schedule.")
        fmt = options.get("format") or "post"
        if fmt not in ("post", "reel"):
            raise ValueError("Choose Post or Reel")
        if fmt == "reel":
            problems = reel_problems(article.video)
            if problems:
                raise ValueError(" ".join(problems))
            _check_file(article)
        return {k: v for k, v in {"format": fmt, "message": message}.items() if v is not None}
    if dest.kind == "instagram":
        if not is_connected(dest):
            raise ValueError("Not connected yet. Open Publishing → Destinations and click Connect.")
        if mode == "draft":
            raise ValueError("Instagram has no drafts. Choose Publish now or Schedule.")
        fmt = options.get("format") or "post"
        if fmt not in ("post", "reel"):
            raise ValueError("Choose Post or Reel")
        if fmt == "reel":
            problems = reel_problems(article.video)
            if problems:
                raise ValueError(" ".join(problems))
            _check_file(article)
            if not public_video_url(article):
                raise ValueError("Instagram Reels need Samyabadi on a public https address, because Instagram "
                                 "downloads the video from it.")
        elif article.featured_image is None:
            raise ValueError("Instagram needs a picture. Add a featured image to the article.")
        elif not public_image_url(article):
            raise ValueError(IG_PUBLIC_HINT)
        return {k: v for k, v in {"format": fmt, "message": message}.items() if v is not None}
    if dest.kind == "website":
        from app.publishing.website import website_problem

        problem = website_problem(dest, mode)
        if problem:
            raise ValueError(problem)
        return {}
    if dest.kind == "youtube":
        if not is_connected(dest):
            raise ValueError("Not connected yet. Open Publishing → Destinations and click Connect.")
        if article.video is None:
            raise ValueError("YouTube needs a video. Upload one in the article's Video box.")
        _check_file(article)
        privacy = options.get("privacy") or "public"
        if privacy not in ("public", "unlisted", "private"):
            raise ValueError("Choose Public, Unlisted or Private")
        return {k: v for k, v in {"privacy": privacy, "message": message}.items() if v is not None}
    return {}


def _check_file(article: Article) -> None:
    try:
        require_file(article.video)
    except VideoError as exc:
        raise ValueError(str(exc)) from exc


def is_slow(dest: PublishingDestination, options: dict | None, *, has_video: bool = False) -> bool:
    """Video uploads can take minutes; they run in the background instead of inside the request
    (a proxy would otherwise time out while the upload carries on)."""
    if dest.kind == "website":
        # The robot browser takes a while; a WordPress site found behind the login is as fast as WordPress.
        return (dest.config or {}).get("platform") != "wordpress" or has_video
    return (dest.kind == "youtube" or (dest.kind in META_KINDS and (options or {}).get("format") == "reel")
            or (dest.kind == "wordpress" and has_video))


__all__ = [
    "FacebookConnector", "InstagramConnector", "YouTubeConnector", "OAuthError", "RemoteGone", "SOCIAL_KINDS",
    "META_KINDS", "app_credentials", "authorize_url", "choose_page", "clear_connection", "complete_oauth",
    "is_connected", "is_slow", "load_secrets", "make_state", "meta_app", "read_state", "redirect_uri",
    "save_meta_app", "save_secrets", "set_config", "uses_shared_app", "validate_options",
]
