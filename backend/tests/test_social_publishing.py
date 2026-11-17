"""Video uploads, Facebook Page and YouTube publishing, and publishing to several places at once.

Facebook's Graph API, its video upload host, Google's OAuth endpoints and the YouTube Data API
are replaced by an in-process fake (FakePlatforms) that records every request.
"""

from __future__ import annotations

import io
import json
import shutil
import struct
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from PIL import Image

from app.core.config import get_settings
from app.core.db import utcnow
from app.models import ArticlePublication, PublishingJob
from app.videos import service as video_service
from app.videos.mp4 import NotAVideo, probe
from tests.test_api import LONG, client, login, make_user, setup_admin, web  # noqa: F401  (fixtures)

GRAPH = "https://graph.facebook.com/v23.0"
RUPLOAD = "https://rupload.facebook.com/video-upload/v23.0"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
YT = "https://www.googleapis.com/youtube/v3"
YT_UP = "https://www.googleapis.com/upload/youtube/v3"
SITE = "https://site.example.net/hook"
SITE_URL = "https://site.example.net/news/garment-factories"


# --- a minimal MP4 / MOV writer ----------------------------------------------------------------


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def mvhd(timescale: int, duration: int, v1: bool = False) -> bytes:
    if v1:
        return box(b"mvhd", bytes([1, 0, 0, 0]) + struct.pack(">QQIQ", 0, 0, timescale, duration) + bytes(80))
    return box(b"mvhd", bytes(4) + struct.pack(">IIII", 0, 0, timescale, duration) + bytes(80))


def tkhd(w: int, h: int, rotate: bool = False) -> bytes:
    body = bytes(4) + struct.pack(">IIIII", 0, 0, 1, 0, 0) + bytes(16)
    neg = (-0x10000) & 0xFFFFFFFF
    matrix = (0, 0x10000, 0, neg, 0, 0, 0, 0, 0x40000000) if rotate else (0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
    return box(b"tkhd", body + struct.pack(">9I", *matrix) + struct.pack(">II", w << 16, h << 16))


def hdlr(kind: bytes) -> bytes:
    return box(b"hdlr", bytes(8) + kind + bytes(12) + b"Handler\x00")


def mp4(w=1080, h=1920, seconds=12.0, *, rotate=False, quicktime=False, moov_last=False, v1=False, mdat_size=6000) -> bytes:
    ftyp = box(b"ftyp", (b"qt  " if quicktime else b"isom") + struct.pack(">I", 512) + b"isomiso2avc1mp41")
    audio = box(b"trak", tkhd(0, 0) + box(b"mdia", hdlr(b"soun")))
    video = box(b"trak", tkhd(w, h, rotate) + box(b"mdia", hdlr(b"vide")))
    moov = box(b"moov", mvhd(1000, int(seconds * 1000), v1) + audio + video)
    mdat = box(b"mdat", bytes(range(256)) * (mdat_size // 256))
    return ftyp + (mdat + moov if moov_last else moov + mdat)


def test_probe_reads_size_duration_and_rotation():
    data = mp4(1080, 1920, 12.5)
    info = probe(io.BytesIO(data), len(data))
    assert (info.mime, info.extension, info.width, info.height, info.duration_seconds) == ("video/mp4", "mp4", 1080, 1920, 12.5)
    # A phone's portrait video is often stored landscape with a 90° rotation matrix.
    data = mp4(1920, 1080, 30, rotate=True)
    info = probe(io.BytesIO(data), len(data))
    assert (info.width, info.height) == (1080, 1920)
    # QuickTime brand, movie header after the media data, 64-bit header fields.
    data = mp4(1280, 720, 95.25, quicktime=True, moov_last=True, v1=True)
    info = probe(io.BytesIO(data), len(data))
    assert (info.mime, info.extension, info.width, info.height, info.duration_seconds) == ("video/quicktime", "mov", 1280, 720, 95.25)


@pytest.mark.parametrize("data", [b"", b"hello world, not a video at all", b"\xff\xd8\xff\xe0" + bytes(200)])
def test_probe_rejects_other_files(data):
    with pytest.raises(NotAVideo):
        probe(io.BytesIO(data), len(data))


def test_probe_rejects_a_file_cut_short():
    data = box(b"ftyp", b"isom" + bytes(4)) + box(b"mdat", bytes(1000))[:600]
    with pytest.raises(NotAVideo, match="incomplete"):
        probe(io.BytesIO(data), len(data))


# --- fake platforms ---------------------------------------------------------------------------------


class FakePlatforms:
    """Facebook Graph + rupload, Google OAuth and YouTube, plus a webhook website."""

    def __init__(self, web):
        self.web = web
        self.calls: list[tuple[str, str]] = []
        self.requests: list[httpx.Request] = []
        self.pages = [{"id": "111", "name": "Dhaka Daily", "access_token": "PAGE-TOKEN-111", "tasks": ["CREATE_CONTENT"]}]
        self.fb_fail: dict | None = None
        self.reel_status = "processing"
        self.yt_privacy_override: str | None = None
        self.yt_put_fail_once = False
        self.yt_token_error: str | None = None
        self.yt_upload_status = "uploaded"
        self.yt_videos: dict[str, dict] = {}
        self.site_fail = 0
        self.refresh_tokens = True
        self.counter = 0
        web.handler = self.handle  # the patched make_client uses web.handler

    def _id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}{self.counter}"

    @staticmethod
    def form(request: httpx.Request) -> dict:
        return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}

    def handle(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        url = str(request.url)
        self.calls.append((request.method, url))
        if url.startswith(GRAPH):
            return self.graph(request, url[len(GRAPH):])
        if url.startswith(RUPLOAD):
            assert request.headers["authorization"] == "OAuth PAGE-TOKEN-111"
            assert int(request.headers["file_size"]) == len(request.content) and request.headers["offset"] == "0"
            return httpx.Response(200, json={"success": True})
        if url == GOOGLE_TOKEN:
            return self.google_token(request)
        if url.startswith(YT_UP) or url.startswith(YT) or url.startswith("https://upload.session.example/"):
            return self.youtube(request, url)
        if url == SITE:
            if self.site_fail:
                self.site_fail -= 1
                return httpx.Response(503, text="down")
            return httpx.Response(200, json={"id": "site-1", "url": SITE_URL})
        return self.web.routes.get(url) and httpx.Response(200) or httpx.Response(404, text="no route")

    # Facebook ---------------------------------------------------------------------------------------
    def graph(self, request: httpx.Request, path: str) -> httpx.Response:
        if path == "/oauth/access_token":
            f = self.form(request)
            assert f["client_id"] == "APP-1" and f["client_secret"] == "APP-SECRET"
            if f.get("grant_type") == "fb_exchange_token":
                return httpx.Response(200, json={"access_token": "LONG-USER"})
            assert f["code"] == "good-code" and f["redirect_uri"].endswith("/api/v1/publishing/oauth/callback")
            return httpx.Response(200, json={"access_token": "SHORT-USER"})
        if path.startswith("/me/accounts"):
            assert request.headers["authorization"] == "Bearer LONG-USER"
            return httpx.Response(200, json={"data": self.pages})
        if self.fb_fail is not None:
            return httpx.Response(400, json={"error": self.fb_fail})
        assert request.headers["authorization"] == "Bearer PAGE-TOKEN-111", path
        if path == "/111/feed":
            return httpx.Response(200, json={"id": self._id("111_")})
        if path == "/111/photos":
            assert b'name="source"' in request.content
            return httpx.Response(200, json={"id": "photo9", "post_id": self._id("111_")})
        if path == "/111/video_reels":
            f = self.form(request)
            if f["upload_phase"] == "start":
                return httpx.Response(200, json={"video_id": "reel77", "upload_url": f"{RUPLOAD}/reel77"})
            assert f["upload_phase"] == "finish" and f["video_id"] == "reel77" and f["video_state"] == "PUBLISHED"
            return httpx.Response(200, json={"success": True})
        if path == "/111?fields=id,name":
            return httpx.Response(200, json={"id": "111", "name": "Dhaka Daily"})
        if path.endswith("?fields=permalink_url"):
            obj = path[1:].split("?")[0]
            return httpx.Response(200, json={"permalink_url": f"https://www.facebook.com/permalink/{obj}"})
        if path.startswith("/reel77?fields=status"):
            ready = self.reel_status == "ready"
            return httpx.Response(200, json={"status": {
                "video_status": self.reel_status,
                "publishing_phase": {"status": "complete" if ready else "not_started"}}, "permalink_url": "/reel/reel77"})
        if request.method in ("POST", "DELETE") and path.count("/") == 1:
            return httpx.Response(200, json={"success": True})
        return httpx.Response(404, json={"error": {"code": 100, "error_subcode": 33, "message": "Unsupported request"}})

    # Google / YouTube ----------------------------------------------------------------------------------
    def google_token(self, request: httpx.Request) -> httpx.Response:
        f = self.form(request)
        assert f["client_id"] == "GCLIENT" and f["client_secret"] == "GSECRET"
        if f["grant_type"] == "authorization_code":
            body = {"access_token": "YA-ACCESS", "expires_in": 3599}
            if self.refresh_tokens:
                body["refresh_token"] = "REFRESH-1"
            return httpx.Response(200, json=body)
        assert f["grant_type"] == "refresh_token" and f["refresh_token"] == "REFRESH-1"
        if self.yt_token_error:
            return httpx.Response(400, json={"error": self.yt_token_error, "error_description": "Token has been expired or revoked."})
        return httpx.Response(200, json={"access_token": "YA-ACCESS", "expires_in": 3599})

    def youtube(self, request: httpx.Request, url: str) -> httpx.Response:
        if not url.startswith("https://upload.session.example/"):
            assert request.headers["authorization"] == "Bearer YA-ACCESS"
        if url.startswith(f"{YT}/channels"):
            return httpx.Response(200, json={"items": [{"id": "UC123", "snippet": {"title": "Dhaka Daily TV"}}]})
        if url.startswith(f"{YT_UP}/videos?uploadType=resumable"):
            meta = json.loads(request.content)
            self.last_meta = meta
            self.upload_size = int(request.headers["x-upload-content-length"])
            return httpx.Response(200, headers={"Location": "https://upload.session.example/s1"})
        if url == "https://upload.session.example/s1":
            size = self.upload_size
            if request.headers.get("content-range") == f"bytes */{size}":
                return httpx.Response(308, headers={"Range": "bytes=0-999"})
            if self.yt_put_fail_once:
                self.yt_put_fail_once = False
                return httpx.Response(503, text="try again")
            rng = request.headers.get("content-range")
            if rng:
                assert rng == f"bytes 1000-{size - 1}/{size}" and len(request.content) == size - 1000
            else:
                assert len(request.content) == size
            vid = self._id("vid")
            privacy = self.yt_privacy_override or self.last_meta["status"]["privacyStatus"]
            self.yt_videos[vid] = {"privacyStatus": privacy, "uploadStatus": self.yt_upload_status}
            return httpx.Response(200, json={"id": vid, "status": dict(self.yt_videos[vid])})
        if url.startswith(f"{YT_UP}/thumbnails/set"):
            return httpx.Response(200, json={"items": []})
        if url.startswith(f"{YT}/videos?part=") and request.method == "PUT":
            body = json.loads(request.content)
            v = self.yt_videos.get(body["id"])
            if v is None:
                return httpx.Response(404, json={"error": {"code": 404, "message": "Video not found",
                                                           "errors": [{"reason": "videoNotFound"}]}})
            v["privacyStatus"] = body["status"]["privacyStatus"]
            return httpx.Response(200, json={"id": body["id"], "status": dict(v)})
        if url.startswith(f"{YT}/videos?part=status&id="):
            vid = url.rsplit("=", 1)[1]
            v = self.yt_videos.get(vid)
            return httpx.Response(200, json={"items": [{"id": vid, "status": v}] if v else []})
        return httpx.Response(404, json={"error": {"code": 404, "message": "no route"}})


@pytest.fixture()
def fake(web):
    return FakePlatforms(web)


# --- helpers -------------------------------------------------------------------------------------------


def approved_article(admin, **extra) -> dict:
    a = admin.post("/articles", json={"headline": "Garment factories reopen", "body_html": LONG,
                                      "excerpt": "Workers returned on Monday.", **extra})
    assert a.status_code == 201, a.text
    aid = a.json()["id"]
    admin.post(f"/articles/{aid}/transition", json={"target": "in_review"})
    r = admin.post(f"/articles/{aid}/transition", json={"target": "approved"})
    assert r.status_code == 200, r.text
    return r.json()


def upload_video(admin, data: bytes | None = None, name: str = "clip.mp4") -> dict:
    r = admin.post("/videos", params={"filename": name}, content=data if data is not None else mp4(),
                   headers={"Content-Type": "video/mp4"})
    assert r.status_code == 201, r.text
    return r.json()


def callback(client, url: str, **params):
    return client.get("/api/v1/publishing/oauth/callback", params=params, follow_redirects=False)


def state_of(authorize_url: str) -> str:
    return parse_qs(urlsplit(authorize_url).query)["state"][0]


def connect_facebook(client, admin, name="Facebook Page") -> dict:
    d = admin.post("/publishing/destinations", json={"name": name, "kind": "facebook", "client_id": "APP-1",
                                                     "secret": "APP-SECRET"})
    assert d.status_code == 201, d.text
    start = admin.post(f"/publishing/destinations/{d.json()['id']}/connect").json()
    r = callback(client, "", state=state_of(start["authorize_url"]), code="good-code")
    assert r.status_code == 303 and "connected=" in r.headers["location"], r.headers.get("location")
    return next(x for x in admin.get("/publishing/destinations").json() if x["id"] == d.json()["id"])


def connect_youtube(client, admin) -> dict:
    d = admin.post("/publishing/destinations", json={"name": "YouTube", "kind": "youtube", "client_id": "GCLIENT",
                                                     "secret": "GSECRET"}).json()
    start = admin.post(f"/publishing/destinations/{d['id']}/connect").json()
    r = callback(client, "", state=state_of(start["authorize_url"]), code="g-code")
    assert r.status_code == 303 and "connected=" in r.headers["location"], r.headers.get("location")
    return next(x for x in admin.get("/publishing/destinations").json() if x["id"] == d["id"])


def website(admin) -> dict:
    return admin.post("/publishing/destinations", json={"name": "My website", "kind": "webhook", "base_url": SITE}).json()


# --- video library -------------------------------------------------------------------------------------


def test_video_upload_attach_and_delete(client):
    admin = setup_admin(client)
    v = upload_video(admin, mp4(1920, 1080, 42.0), "Dhaka rain.mp4")
    assert (v["width"], v["height"], v["duration_seconds"], v["mime"]) == (1920, 1080, 42.0, "video/mp4")
    assert v["title"] == "Dhaka rain" and v["url"].startswith("/media/videos/") and v["url"].endswith(".mp4")
    assert any("vertical" in p for p in v["reel_problems"]) and v["is_short"] is False
    path = get_settings().images_dir / v["url"].removeprefix("/media/")
    assert path.read_bytes() == mp4(1920, 1080, 42.0)
    assert client.get(v["url"]).content == path.read_bytes()

    a = admin.post("/articles", json={"headline": "Rain", "body_html": LONG, "video_id": v["id"]}).json()
    assert a["video"]["id"] == v["id"]
    preview = admin.get(f"/articles/{a['id']}/preview")
    assert f'<video controls preload="metadata" src="{v["url"]}">' in preview.text
    assert "media-src 'self'" in preview.headers["content-security-policy"]
    assert admin.delete(f"/videos/{v['id']}").status_code == 409  # still in an article

    cleared = admin.put(f"/articles/{a['id']}", json={"video_id": None, "version": a["version"]}).json()
    assert cleared["video"] is None and cleared["version"] == a["version"] + 1
    assert admin.delete(f"/videos/{v['id']}").json() == {"ok": True}
    assert not path.exists() and admin.get(f"/videos/{v['id']}").status_code == 404


def test_video_upload_rejects_bad_files_and_needs_csrf(client, monkeypatch):
    admin = setup_admin(client)
    bad = admin.post("/videos", params={"filename": "x.mp4"}, content=b"not a video" * 50)
    assert bad.status_code == 422 and "not an MP4" in bad.json()["error"]["message"]
    monkeypatch.setattr(get_settings(), "max_video_bytes", 2000)
    big = admin.post("/videos", params={"filename": "big.mp4"}, content=mp4(mdat_size=5000))
    assert big.status_code == 413 and big.json()["error"]["code"] == "payload_too_large"
    leftovers = [p for d in (get_settings().videos_dir, video_service.uploads_dir()) for p in d.rglob("*") if p.is_file()]
    assert leftovers == []  # partial uploads are removed
    no_csrf = client.post("/api/v1/videos", content=mp4())
    assert no_csrf.status_code == 403


@pytest.fixture(autouse=True)
def _no_unfinished_uploads():
    """Storage is shared by the whole test run; each test starts without other tests' unfinished uploads."""
    shutil.rmtree(video_service.uploads_dir(), ignore_errors=True)
    yield


def test_upload_in_pieces(client):
    admin = setup_admin(client)
    data = mp4(1080, 1920, 20.0, mdat_size=25_000)
    start = admin.post("/videos/uploads", json={"filename": "Flood.mp4", "size": len(data)}).json()
    assert start["received"] == 0 and start["chunk_size"] == 8 * 1024 * 1024 and start["size"] == len(data)
    uid = start["upload_id"]
    assert admin.put(f"/videos/uploads/{uid}", params={"offset": 0}, content=data[:10_000]).json()["received"] == 10_000

    # The connection dropped after part of the next piece arrived: the server keeps that prefix,
    # and a piece sent from the wrong place is refused with the size to continue from.
    path = video_service.uploads_dir() / f"{uid}.part"
    with open(path, "ab") as f:
        f.write(data[10_000:12_345])
    assert admin.get(f"/videos/uploads/{uid}").json()["received"] == 12_345
    stale = admin.put(f"/videos/uploads/{uid}", params={"offset": 10_000}, content=data[10_000:20_000])
    assert stale.status_code == 409 and stale.json()["error"]["details"] == {"received": 12_345}
    early = admin.post(f"/videos/uploads/{uid}/complete")
    assert early.status_code == 409 and "incomplete" in early.json()["error"]["message"]

    assert admin.put(f"/videos/uploads/{uid}", params={"offset": 12_345}, content=data[12_345:]).json()["received"] == len(data)
    v = admin.post(f"/videos/uploads/{uid}/complete").json()
    assert (v["title"], v["width"], v["height"], v["duration_seconds"], v["is_short"]) == ("Flood", 1080, 1920, 20.0, True)
    stored = get_settings().images_dir / v["url"].removeprefix("/media/")
    assert stored.read_bytes() == data
    assert list(video_service.uploads_dir().iterdir()) == []  # nothing left behind
    assert admin.get(f"/videos/uploads/{uid}").status_code == 404  # finished uploads are gone

    # Another user cannot see or touch someone else's upload.
    other = admin.post("/videos/uploads", json={"filename": "x.mp4", "size": 10}).json()
    make_user(admin, "editor@example.com", "editor")
    editor = login(client, "editor@example.com")
    assert editor.put(f"/videos/uploads/{other['upload_id']}", params={"offset": 0}, content=b"0123456789").status_code == 404
    assert editor.get(f"/videos/uploads/../../secret").status_code == 404


def test_upload_in_pieces_refuses_bad_input_and_cleans_up(client, monkeypatch):
    admin = setup_admin(client)
    monkeypatch.setattr(get_settings(), "max_video_bytes", 50_000)
    too_big = admin.post("/videos/uploads", json={"filename": "big.mp4", "size": 50_001})
    assert too_big.status_code == 413 and too_big.json()["error"]["code"] == "payload_too_large"
    assert admin.post("/videos/uploads", json={"filename": "e.mp4", "size": 0}).status_code == 422

    # More data than announced is refused and the piece is not kept.
    up = admin.post("/videos/uploads", json={"filename": "a.mp4", "size": 100}).json()
    over = admin.put(f"/videos/uploads/{up['upload_id']}", params={"offset": 0}, content=bytes(101))
    assert over.status_code == 422 and admin.get(f"/videos/uploads/{up['upload_id']}").json()["received"] == 0

    # A complete file that is not a video is rejected at the end and removed.
    junk = b"this is not a video file " * 4
    up = admin.post("/videos/uploads", json={"filename": "notes.mp4", "size": len(junk)}).json()
    admin.put(f"/videos/uploads/{up['upload_id']}", params={"offset": 0}, content=junk)
    bad = admin.post(f"/videos/uploads/{up['upload_id']}/complete")
    assert bad.status_code == 422 and "not an MP4" in bad.json()["error"]["message"]

    # Cancelling removes the files; abandoned uploads are pruned by housekeeping.
    up = admin.post("/videos/uploads", json={"filename": "c.mp4", "size": 10}).json()
    assert admin.delete(f"/videos/uploads/{up['upload_id']}").json() == {"ok": True}
    stale = admin.post("/videos/uploads", json={"filename": "s.mp4", "size": 10}).json()
    assert video_service.prune_uploads(max_age_seconds=3600) == 0
    assert video_service.prune_uploads(max_age_seconds=-1) == 2  # "s.mp4" and the abandoned "a.mp4"
    assert admin.get(f"/videos/uploads/{stale['upload_id']}").status_code == 404
    assert list(video_service.uploads_dir().iterdir()) == []
    assert client.post("/api/v1/videos/uploads", json={"size": 10}).status_code == 403  # CSRF


# --- connecting -----------------------------------------------------------------------------------------


def test_facebook_connect_with_page_choice(client, fake):
    admin = setup_admin(client)
    fake.pages.append({"id": "222", "name": "Sylhet Times", "access_token": "PAGE-TOKEN-222", "tasks": ["MANAGE"]})
    fake.pages.append({"id": "333", "name": "Read only", "access_token": "T3", "tasks": ["ANALYZE"]})
    d = admin.post("/publishing/destinations", json={"name": "FB", "kind": "facebook", "client_id": "APP-1",
                                                     "secret": "APP-SECRET"})
    assert d.status_code == 201
    body = d.json()
    assert body["connection"]["connected"] is False and body["has_secret"] is True
    assert body["connection"]["redirect_uri"].endswith("/api/v1/publishing/oauth/callback")
    assert "APP-SECRET" not in d.text

    start = admin.post(f"/publishing/destinations/{body['id']}/connect").json()
    q = parse_qs(urlsplit(start["authorize_url"]).query)
    assert start["authorize_url"].startswith("https://www.facebook.com/v23.0/dialog/oauth?")
    assert q["client_id"] == ["APP-1"] and "pages_manage_posts" in q["scope"][0]
    state = q["state"][0]
    r = callback(client, "", state=state, code="good-code")
    assert r.status_code == 303 and r.headers["location"].startswith("/publishing?tab=destinations&connected=")
    dest = next(x for x in admin.get("/publishing/destinations").json() if x["id"] == body["id"])
    assert dest["connection"]["connected"] is False
    assert [p["name"] for p in dest["connection"]["pages"]] == ["Dhaka Daily", "Sylhet Times"]  # not the read-only one
    assert "PAGE-TOKEN" not in json.dumps(dest)

    chosen = admin.post(f"/publishing/destinations/{body['id']}/facebook-page", json={"page_id": "222"}).json()
    assert chosen["connection"]["connected"] is True and chosen["connection"]["account_name"] == "Sylhet Times"
    assert chosen["connection"]["pages"] == [] and chosen["last_check_status"].startswith("OK: Connected")

    again = callback(client, "", state=state, code="good-code")  # a state works once
    assert "connect_error=" in again.headers["location"] and "already+used" in again.headers["location"]
    forged = callback(client, "", state=state[:-3] + "abc", code="good-code")
    assert "connect_error=" in forged.headers["location"]


def test_facebook_cancelled_and_misconfigured(client, fake):
    admin = setup_admin(client)
    missing = admin.post("/publishing/destinations", json={"name": "FB", "kind": "facebook", "client_id": "APP-1"})
    assert missing.status_code == 422
    d = admin.post("/publishing/destinations", json={"name": "FB", "kind": "facebook", "client_id": "APP-1",
                                                     "secret": "APP-SECRET"}).json()
    start = admin.post(f"/publishing/destinations/{d['id']}/connect").json()
    r = callback(client, "", state=state_of(start["authorize_url"]), error="access_denied", error_reason="user_denied")
    assert "cancelled" in r.headers["location"]
    fake.pages = []
    start = admin.post(f"/publishing/destinations/{d['id']}/connect").json()
    r = callback(client, "", state=state_of(start["authorize_url"]), code="good-code")
    assert "connect_error=" in r.headers["location"] and "any+Page" in r.headers["location"]
    test = admin.post(f"/publishing/destinations/{d['id']}/test").json()
    assert test["ok"] is False and "not connected" in test["message"]


def test_youtube_connect(client, fake):
    admin = setup_admin(client)
    fake.refresh_tokens = False
    d = admin.post("/publishing/destinations", json={"name": "YouTube", "kind": "youtube", "client_id": "GCLIENT",
                                                     "secret": "GSECRET"}).json()
    start = admin.post(f"/publishing/destinations/{d['id']}/connect").json()
    q = parse_qs(urlsplit(start["authorize_url"]).query)
    assert q["access_type"] == ["offline"] and q["prompt"] == ["consent"] and "youtube" in q["scope"][0]
    r = callback(client, "", state=q["state"][0], code="g-code")
    assert "lasting+connection" in r.headers["location"]
    fake.refresh_tokens = True
    dest = connect_youtube(client, admin)
    assert dest["connection"]["account_name"] == "Dhaka Daily TV"
    assert dest["connection"]["account_url"] == "https://www.youtube.com/channel/UC123"
    assert admin.post(f"/publishing/destinations/{dest['id']}/test").json() == {
        "ok": True, "message": "Connected to YouTube channel “Dhaka Daily TV”"}
    off = admin.post(f"/publishing/destinations/{dest['id']}/disconnect").json()
    assert off["connection"]["connected"] is False and off["has_secret"] is True


# --- publishing ------------------------------------------------------------------------------------------


def test_website_and_facebook_post_together_links_the_article(client, fake):
    admin = setup_admin(client)
    site = website(admin)
    fb = connect_facebook(client, admin)
    art = approved_article(admin)
    r = admin.post(f"/articles/{art['id']}/publish-many", json={"targets": [
        {"destination_id": fb["id"], "options": {"format": "post"}},
        {"destination_id": site["id"]},
    ]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [j["status"] for j in body["jobs"]] == ["succeeded", "succeeded"]
    assert [j["destination_kind"] for j in body["jobs"]] == ["webhook", "facebook"]  # website first
    feed = next(q for q in fake.requests if str(q.url) == f"{GRAPH}/111/feed")
    form = fake.form(feed)
    assert form["link"] == SITE_URL and form["message"] == "Garment factories reopen\n\nWorkers returned on Monday."
    pubs = {p["destination_kind"]: p for p in body["article"]["publications"]}
    assert pubs["facebook"]["remote_status"] == "published"
    assert pubs["facebook"]["remote_url"].startswith("https://www.facebook.com/permalink/111_")
    assert body["article"]["status"] == "published"

    # Publishing again updates the same post instead of posting twice.
    again = admin.post(f"/articles/{art['id']}/publish-many", json={"targets": [
        {"destination_id": fb["id"], "options": {"format": "post", "message": "Updated caption"}}]}).json()
    assert again["jobs"][0]["status"] == "succeeded"
    post_id = pubs["facebook"]["remote_id"]
    edit = [q for q in fake.requests if str(q.url) == f"{GRAPH}/{post_id}" and q.method == "POST"]
    assert edit and fake.form(edit[-1])["message"] == "Updated caption"

    un = admin.post(f"/articles/{art['id']}/unpublish", json={"destination_id": fb["id"]}).json()
    assert un["job"]["status"] == "succeeded"
    assert ("DELETE", f"{GRAPH}/{post_id}") in fake.calls


def test_facebook_photo_post_without_website(client, fake):
    admin = setup_admin(client)
    fb = connect_facebook(client, admin)
    buf = io.BytesIO()
    Image.new("RGB", (900, 600), (10, 120, 60)).save(buf, "JPEG")
    img = admin.post("/images/upload", files={"file": ("photo.jpg", buf.getvalue(), "image/jpeg")},
                     data={"license_code": "cc0", "title": "Factory"}).json()
    admin.post(f"/images/{img['id']}/approve")
    art = approved_article(admin, featured_image_id=img["id"])
    r = admin.post(f"/articles/{art['id']}/publish-many", json={"targets": [{"destination_id": fb["id"]}]}).json()
    assert r["jobs"][0]["status"] == "succeeded", r["jobs"][0]["last_error"]
    assert ("POST", f"{GRAPH}/111/photos") in fake.calls and ("POST", f"{GRAPH}/111/feed") not in fake.calls


def test_facebook_reel_upload_and_processing(client, fake):
    admin = setup_admin(client)
    fb = connect_facebook(client, admin)
    v = upload_video(admin, mp4(1080, 1920, 15))
    art = approved_article(admin, video_id=v["id"])
    r = admin.post(f"/articles/{art['id']}/publish-many", json={"targets": [
        {"destination_id": fb["id"], "options": {"format": "reel", "message": "Watch: factories reopen"}}]}).json()
    assert r["in_progress"] == ["Facebook Page"]
    # The upload runs after the response (the test client waits for it).
    job = admin.get("/publishing/jobs", params={"article_id": art["id"]}).json()["items"][0]
    assert job["status"] == "succeeded", job
    upload = next(q for q in fake.requests if str(q.url) == f"{RUPLOAD}/reel77")
    assert upload.content == mp4(1080, 1920, 15)
    finish = [fake.form(q) for q in fake.requests if str(q.url) == f"{GRAPH}/111/video_reels"][-1]
    assert finish["description"] == "Watch: factories reopen"
    pub = admin.get(f"/articles/{art['id']}").json()["publications"][0]
    assert pub["remote_status"] == "processing" and pub["remote_id"] == "reel77"

    fake.reel_status = "ready"
    synced = admin.post(f"/publishing/publications/{pub['id']}/sync").json()
    assert synced["remote_status"] == "published" and synced["remote_url"] == "https://www.facebook.com/reel/reel77"


def test_reel_and_draft_rules_reject_the_whole_request(client, fake):
    admin = setup_admin(client)
    site = website(admin)
    fb = connect_facebook(client, admin)
    v = upload_video(admin, mp4(1920, 1080, 15))
    art = approved_article(admin, video_id=v["id"])
    r = admin.post(f"/articles/{art['id']}/publish-many", json={"targets": [
        {"destination_id": site["id"]}, {"destination_id": fb["id"], "options": {"format": "reel"}}]})
    assert r.status_code == 422 and r.json()["error"]["message"].startswith("Facebook Page: Reels must be vertical")
    assert admin.get("/publishing/jobs").json()["total"] == 0  # nothing queued, not even the website
    long = upload_video(admin, mp4(1080, 1920, 120))
    art2 = approved_article(admin, video_id=long["id"])
    r = admin.post(f"/articles/{art2['id']}/publish-many", json={"targets": [
        {"destination_id": fb["id"], "options": {"format": "reel"}}]})
    assert r.status_code == 422 and "at most 90 seconds" in r.json()["error"]["message"]
    draft = admin.post(f"/articles/{art['id']}/publish-many", json={"mode": "draft", "targets": [{"destination_id": fb["id"]}]})
    assert draft.status_code == 422 and "no drafts" in draft.json()["error"]["message"]
    dup = admin.post(f"/articles/{art['id']}/publish-many", json={"targets": [{"destination_id": site["id"]}] * 2})
    assert dup.status_code == 422


def test_youtube_upload_unpublish_and_republish(client, fake):
    admin = setup_admin(client)
    site = website(admin)
    yt = connect_youtube(client, admin)
    no_video = approved_article(admin)
    r = admin.post(f"/articles/{no_video['id']}/publish-many", json={"targets": [{"destination_id": yt["id"]}]})
    assert r.status_code == 422 and "needs a video" in r.json()["error"]["message"]

    v = upload_video(admin, mp4(1920, 1080, 75))
    art = approved_article(admin, video_id=v["id"])
    admin.put(f"/articles/{art['id']}", json={"tags": ["Garments", "Dhaka <city>"], "version": art["version"]})
    r = admin.post(f"/articles/{art['id']}/publish-many", json={"targets": [
        {"destination_id": yt["id"], "options": {"privacy": "unlisted"}}, {"destination_id": site["id"]}]}).json()
    assert r["in_progress"] == ["YouTube"]
    meta = fake.last_meta
    assert meta["snippet"]["title"] == "Garment factories reopen" and meta["snippet"]["categoryId"] == "25"
    assert meta["snippet"]["description"] == f"Workers returned on Monday.\n\n{SITE_URL}"
    assert sorted(meta["snippet"]["tags"]) == ["Dhaka ‹city›", "Garments"]
    assert meta["status"] == {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": False}
    assert fake.upload_size == len(mp4(1920, 1080, 75))
    pub = next(p for p in admin.get(f"/articles/{art['id']}").json()["publications"] if p["destination_kind"] == "youtube")
    assert pub["remote_status"] == "processing" and pub["remote_url"] == f"https://www.youtube.com/watch?v={pub['remote_id']}"

    fake.yt_videos[pub["remote_id"]]["uploadStatus"] = "processed"
    assert admin.post(f"/publishing/publications/{pub['id']}/sync").json()["remote_status"] == "published"
    un = admin.post(f"/articles/{art['id']}/unpublish", json={"destination_id": yt["id"]}).json()
    assert un["job"]["status"] == "succeeded" and fake.yt_videos[pub["remote_id"]]["privacyStatus"] == "private"
    uploads = sum(1 for m, u in fake.calls if u == "https://upload.session.example/s1")
    again = admin.post(f"/articles/{art['id']}/publish-many", json={"targets": [{"destination_id": yt["id"]}]}).json()
    job = admin.get("/publishing/jobs", params={"article_id": art["id"]}).json()["items"][0]
    assert job["status"] == "succeeded" and again["jobs"][0]["destination_kind"] == "youtube"
    assert fake.yt_videos[pub["remote_id"]]["privacyStatus"] == "public"
    assert sum(1 for m, u in fake.calls if u == "https://upload.session.example/s1") == uploads  # no second upload


def test_youtube_resumes_an_interrupted_upload_and_reports_forced_private(client, fake):
    admin = setup_admin(client)
    yt = connect_youtube(client, admin)
    v = upload_video(admin, mp4(1080, 1920, 50, mdat_size=8000))
    art = approved_article(admin, video_id=v["id"])
    fake.yt_put_fail_once = True
    fake.yt_privacy_override = "private"  # what an unaudited Google Cloud project gets
    admin.post(f"/articles/{art['id']}/publish-many", json={"targets": [{"destination_id": yt["id"]}]})
    job = admin.get("/publishing/jobs", params={"article_id": art["id"]}).json()["items"][0]
    assert job["status"] == "succeeded", job["last_error"]
    pub = admin.get(f"/articles/{art['id']}").json()["publications"][0]
    assert pub["remote_status"] == "private" and "audit" in pub["warning"]
    assert pub["remote_url"].startswith("https://www.youtube.com/shorts/")  # vertical and under 3 minutes


def test_expired_connections_fail_clearly_without_retrying(client, fake):
    admin = setup_admin(client)
    yt = connect_youtube(client, admin)
    fb = connect_facebook(client, admin)
    v = upload_video(admin)
    art = approved_article(admin, video_id=v["id"])
    fake.yt_token_error = "invalid_grant"
    fake.fb_fail = {"code": 190, "message": "Error validating access token", "type": "OAuthException"}
    admin.post(f"/articles/{art['id']}/publish-many", json={"targets": [
        {"destination_id": yt["id"]}, {"destination_id": fb["id"], "options": {"format": "post"}}]})
    jobs = {j["destination_kind"]: j for j in admin.get("/publishing/jobs", params={"article_id": art["id"]}).json()["items"]}
    assert jobs["youtube"]["status"] == "failed" and "Connect the channel again" in jobs["youtube"]["last_error"]
    assert jobs["facebook"]["status"] == "failed" and "connect the Page again" in jobs["facebook"]["last_error"]
    assert jobs["youtube"]["attempts"] == 1 and jobs["facebook"]["attempts"] == 1


def test_social_post_waits_for_the_website(client, fake):
    admin = setup_admin(client)
    site = website(admin)
    fb = connect_facebook(client, admin)
    art = approved_article(admin)
    fake.site_fail = 1  # the website is briefly down
    r = admin.post(f"/articles/{art['id']}/publish-many", json={"targets": [
        {"destination_id": site["id"]}, {"destination_id": fb["id"]}]}).json()
    by_kind = {j["destination_kind"]: j for j in r["jobs"]}
    assert by_kind["webhook"]["status"] == "pending" and by_kind["webhook"]["attempts"] == 1
    assert by_kind["facebook"]["status"] == "pending" and by_kind["facebook"]["attempts"] == 0
    assert r["in_progress"] == ["Facebook Page"] and ("POST", f"{GRAPH}/111/feed") not in fake.calls

    from app.core.db import SessionLocal

    s = SessionLocal()
    for job in s.query(PublishingJob).all():
        job.next_attempt_at = utcnow() - timedelta(seconds=1)
    s.commit()
    s.close()
    run = admin.post("/publishing/jobs/run").json()
    assert run["succeeded"] == 2, run
    feed = next(q for q in fake.requests if str(q.url) == f"{GRAPH}/111/feed")
    assert fake.form(feed)["link"] == SITE_URL


def test_scheduling_several_destinations(client, fake):
    admin = setup_admin(client)
    site = website(admin)
    fb = connect_facebook(client, admin)
    art = approved_article(admin)
    when = datetime.now(timezone.utc) + timedelta(hours=2)
    past = admin.post(f"/articles/{art['id']}/publish-many", json={
        "mode": "scheduled", "scheduled_for": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "targets": [{"destination_id": site["id"]}]})
    assert past.status_code == 422
    r = admin.post(f"/articles/{art['id']}/publish-many", json={
        "mode": "scheduled", "scheduled_for": when.isoformat(),
        "targets": [{"destination_id": site["id"]}, {"destination_id": fb["id"], "options": {"format": "post"}}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [j["status"] for j in body["jobs"]] == ["pending", "pending"] and body["in_progress"] == []
    assert body["article"]["status"] == "scheduled"
    assert datetime.fromisoformat(body["article"]["scheduled_at"]) == when.replace(microsecond=when.microsecond)
    assert not any(u.startswith(GRAPH + "/111/") for _, u in fake.calls)
    same = admin.post(f"/articles/{art['id']}/publish-many", json={
        "mode": "scheduled", "scheduled_for": when.isoformat(),
        "targets": [{"destination_id": site["id"]}, {"destination_id": fb["id"], "options": {"format": "post"}}]}).json()
    assert same["duplicate_request"] is True and admin.get("/publishing/jobs").json()["total"] == 2

    fb_job = next(j for j in body["jobs"] if j["destination_kind"] == "facebook")
    admin.post(f"/publishing/jobs/{fb_job['id']}/cancel")
    assert admin.get(f"/articles/{art['id']}").json()["status"] == "scheduled"  # the website is still queued
    site_job = next(j for j in body["jobs"] if j["destination_kind"] == "webhook")
    admin.post(f"/publishing/jobs/{site_job['id']}/cancel")
    after = admin.get(f"/articles/{art['id']}").json()
    assert after["status"] == "approved" and after["scheduled_at"] is None


def test_scheduled_social_post_after_the_article_is_live(client, fake):
    admin = setup_admin(client)
    site = website(admin)
    fb = connect_facebook(client, admin)
    art = approved_article(admin)
    assert admin.post(f"/articles/{art['id']}/publish-many", json={"targets": [{"destination_id": site["id"]}]}).status_code == 200
    when = datetime.now(timezone.utc) + timedelta(hours=1)
    r = admin.post(f"/articles/{art['id']}/publish-many", json={
        "mode": "scheduled", "scheduled_for": when.isoformat(), "targets": [{"destination_id": fb["id"]}]}).json()
    assert r["article"]["status"] == "published" and r["article"]["scheduled_at"] is not None

    from app.core.db import SessionLocal

    s = SessionLocal()
    job = s.get(PublishingJob, __import__("uuid").UUID(r["jobs"][0]["id"]))
    job.next_attempt_at = job.scheduled_for = utcnow() - timedelta(seconds=1)
    s.commit()
    s.close()
    assert admin.post("/publishing/jobs/run").json()["succeeded"] == 1
    feed = next(q for q in fake.requests if str(q.url) == f"{GRAPH}/111/feed")
    assert fake.form(feed)["link"] == SITE_URL
    final = admin.get(f"/articles/{art['id']}").json()
    assert final["status"] == "published" and final["scheduled_at"] is None


def test_wordpress_gets_the_video_once(client, web):
    admin = setup_admin(client)
    api = "https://blog.example.net/wp-json/wp/v2"
    media_uploads: list[bytes] = []
    contents: list[str] = []

    def media(request: httpx.Request):
        media_uploads.append(request.read())
        assert request.headers["content-type"] == "video/mp4"
        return httpx.Response(201, json={"id": 55, "source_url": "https://blog.example.net/wp-content/uploads/clip.mp4"})

    def posts(request: httpx.Request):
        contents.append(json.loads(request.content)["content"])
        return httpx.Response(201, json={"id": 9, "link": "https://blog.example.net/?p=9", "status": "publish"})

    web.routes[f"{api}/media"] = media
    web.routes[f"{api}/posts"] = posts
    web.routes[f"{api}/posts/9"] = posts
    d = admin.post("/publishing/destinations", json={"name": "Blog", "kind": "wordpress", "base_url": "https://blog.example.net",
                                                     "username": "bot", "secret": "abcd efgh"}).json()
    v = upload_video(admin)
    art = approved_article(admin, video_id=v["id"])
    for _ in range(2):
        r = admin.post(f"/articles/{art['id']}/publish-many", json={"targets": [{"destination_id": d["id"]}]}).json()
        assert r["in_progress"] == ["Blog"]  # with a video, the WordPress upload runs in the background
        job = admin.get("/publishing/jobs", params={"article_id": art["id"]}).json()["items"][0]
        assert job["status"] == "succeeded", job["last_error"]
        admin.put(f"/articles/{art['id']}", json={"subtitle": "More", "version": admin.get(f"/articles/{art['id']}").json()["version"]})
    assert media_uploads == [mp4()]
    assert all(c.startswith('<!-- wp:video --><figure class="wp-block-video"><video controls preload="metadata" '
                            'src="https://blog.example.net/wp-content/uploads/clip.mp4">') for c in contents)
    assert len(contents) == 2


def test_jobs_are_claimed_once_and_interrupted_jobs_fail(client, fake, db):
    from app.publishing.service import _claim, recover_interrupted

    admin = setup_admin(client)
    site = website(admin)
    art = approved_article(admin)
    when = datetime.now(timezone.utc) + timedelta(hours=1)
    r = admin.post(f"/articles/{art['id']}/publish-many", json={
        "mode": "scheduled", "scheduled_for": when.isoformat(), "targets": [{"destination_id": site["id"]}]}).json()
    job = db.get(PublishingJob, __import__("uuid").UUID(r["jobs"][0]["id"]))
    assert _claim(db, job) is True and job.status == "running" and job.attempts == 1
    assert _claim(db, job) is False and job.attempts == 1
    job.updated_at = utcnow() - timedelta(hours=4)
    db.commit()
    assert recover_interrupted(db) == 1
    db.commit()
    assert job.status == "failed" and "Interrupted" in job.last_error
    after = admin.get(f"/articles/{art['id']}").json()
    assert after["status"] == "approved" and after["scheduled_at"] is None


def test_moving_back_to_draft_cancels_a_queued_social_post(client, fake):
    admin = setup_admin(client)
    make_user(admin, "writer@example.com", "researcher")
    site = website(admin)
    fb = connect_facebook(client, admin)
    art = approved_article(admin)
    admin.post(f"/articles/{art['id']}/publish-many", json={"targets": [{"destination_id": site["id"]}]})
    when = datetime.now(timezone.utc) + timedelta(hours=1)
    admin.post(f"/articles/{art['id']}/publish-many", json={
        "mode": "scheduled", "scheduled_for": when.isoformat(), "targets": [{"destination_id": fb["id"]}]})
    un = admin.post(f"/articles/{art['id']}/unpublish", json={"destination_id": site["id"]}).json()
    assert un["article"]["status"] == "unpublished"
    writer = login(client, "writer@example.com")
    moved = writer.post(f"/articles/{art['id']}/transition", json={"target": "draft"})
    assert moved.status_code == 200, moved.text
    assert moved.json()["scheduled_at"] is None
    jobs = writer.get("/publishing/jobs", params={"article_id": art["id"], "status": ["cancelled"]}).json()
    assert jobs["total"] == 1 and "moved from unpublished to draft" in jobs["items"][0]["last_error"]
