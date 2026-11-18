"""Publishing with a website's own admin login, the shared Meta app, and Instagram.

* WordPress behind an admin login: an in-process fake WordPress (login form, REST nonce,
  application passwords, posts).
* Any other site: a real Chromium (Playwright) drives a small admin panel served on 127.0.0.1,
  is taught the post form once, and publishes through it. Skipped if Chromium is not installed.
* Facebook and Instagram through the Meta app saved once under Settings → Integrations.
"""

from __future__ import annotations

import base64
import io
import re
import threading
import time
from email.parser import BytesParser
from email.policy import default as email_policy
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from PIL import Image

from app.core.config import get_settings
from app.publishing import website
from tests.test_api import LONG, client, setup_admin, web  # noqa: F401  (fixtures)
from tests.test_social_publishing import GRAPH, FakePlatforms, approved_article, callback, state_of

# --- the shared Meta app and Instagram ---------------------------------------------------------------


class FakeMeta(FakePlatforms):
    """Facebook's fake, plus the Instagram account linked to Page 111."""

    def __init__(self, web):
        super().__init__(web)
        self.pages[0]["instagram_business_account"] = {"id": "IG1", "username": "dhakadaily"}
        self.container_status = "FINISHED"
        self.published: list[dict] = []

    def graph(self, request, path):
        if path == "/oauth/access_token":
            f = self.form(request)
            assert f["client_id"] == "4242" and f["client_secret"] == "SHARED-SECRET"
            if f.get("grant_type") == "fb_exchange_token":
                return httpx.Response(200, json={"access_token": "LONG-USER"})
            return httpx.Response(200, json={"access_token": "SHORT-USER"})
        if path.startswith("/me/accounts"):
            assert "instagram_business_account" in path or "IG" not in path
            return httpx.Response(200, json={"data": self.pages})
        if path.startswith("/IG1") or path.startswith("/container") or path.startswith("/igpost"):
            assert request.headers["authorization"] == "Bearer PAGE-TOKEN-111"
            if path == "/IG1/media":
                f = self.form(request)
                self.published.append(f)
                return httpx.Response(200, json={"id": f"container{len(self.published)}"})
            if path == "/IG1/media_publish":
                return httpx.Response(200, json={"id": "igpost1"})
            if path.startswith("/igpost1?fields=permalink"):
                return httpx.Response(200, json={"permalink": "https://www.instagram.com/p/ABC/"})
            if path.startswith("/container") and "status_code" in path:
                return httpx.Response(200, json={"status_code": self.container_status})
            if path.startswith("/IG1?fields="):
                return httpx.Response(200, json={"id": "IG1", "username": "dhakadaily"})
        return super().graph(request, path)


@pytest.fixture()
def meta(web):
    return FakeMeta(web)


def save_meta_app(admin):
    r = admin.put("/publishing/meta-app", json={"app_id": "4242", "secret": "SHARED-SECRET"})
    assert r.status_code == 200, r.text
    return r.json()


def test_meta_app_is_saved_once_and_never_returned(client, meta):
    admin = setup_admin(client)
    assert admin.get("/publishing/meta-app").json()["configured"] is False
    # Facebook without the shared app and without its own app: explained, not created.
    r = admin.post("/publishing/destinations", json={"name": "FB", "kind": "facebook"})
    assert r.status_code == 422 and "Settings → Integrations" in r.json()["error"]["message"]
    assert admin.put("/publishing/meta-app", json={"app_id": "4242"}).status_code == 422  # secret needed first time
    out = save_meta_app(admin)
    assert out["configured"] is True and out["has_secret"] is True and "SHARED-SECRET" not in str(out)
    assert out["redirect_uri"].endswith("/api/v1/publishing/oauth/callback")
    # Keeping the secret while changing an optional field.
    kept = admin.put("/publishing/meta-app", json={"app_id": "4242", "login_config_id": "cfg9"}).json()
    assert kept["has_secret"] is True and kept["login_config_id"] == "cfg9"


def test_facebook_connects_with_the_shared_app(client, meta):
    admin = setup_admin(client)
    save_meta_app(admin)
    d = admin.post("/publishing/destinations", json={"name": "FB", "kind": "facebook"})
    assert d.status_code == 201, d.text
    assert d.json()["connection"]["shared_app"] is True
    start = admin.post(f"/publishing/destinations/{d.json()['id']}/connect").json()
    q = parse_qs(urlsplit(start["authorize_url"]).query)
    assert q["client_id"] == ["4242"]
    r = callback(client, "", state=state_of(start["authorize_url"]), code="good-code")
    assert "connected=" in r.headers["location"], r.headers["location"]
    fb = next(x for x in admin.get("/publishing/destinations").json() if x["id"] == d.json()["id"])
    assert fb["connection"]["connected"] is True and fb["connection"]["account_name"] == "Dhaka Daily"


def _photo_article(admin):
    buf = io.BytesIO()
    Image.new("RGB", (1080, 1080), (200, 60, 40)).save(buf, "JPEG")
    img = admin.post("/images/upload", files={"file": ("photo.jpg", buf.getvalue(), "image/jpeg")},
                     data={"license_code": "cc0", "title": "Factory"}).json()
    admin.post(f"/images/{img['id']}/approve")
    return approved_article(admin, featured_image_id=img["id"])


def connect_instagram(client, admin):
    d = admin.post("/publishing/destinations", json={"name": "Instagram", "kind": "instagram"})
    assert d.status_code == 201, d.text
    start = admin.post(f"/publishing/destinations/{d.json()['id']}/connect").json()
    q = parse_qs(urlsplit(start["authorize_url"]).query)
    assert "instagram_content_publish" in q["scope"][0]
    r = callback(client, "", state=state_of(start["authorize_url"]), code="good-code")
    assert "connected=" in r.headers["location"], r.headers["location"]
    return next(x for x in admin.get("/publishing/destinations").json() if x["id"] == d.json()["id"])


def test_instagram_connect_and_photo_post(client, meta, monkeypatch):
    admin = setup_admin(client)
    save_meta_app(admin)
    ig = connect_instagram(client, admin)
    assert ig["connection"]["connected"] is True and ig["connection"]["account_name"] == "@dhakadaily"
    assert ig["connection"]["account_url"] == "https://www.instagram.com/dhakadaily/"
    assert "PAGE-TOKEN" not in str(ig)

    # On localhost Instagram cannot download an uploaded photo: refused before anything is queued.
    art = _photo_article(admin)
    r = admin.post(f"/articles/{art['id']}/publish-many", json={"targets": [{"destination_id": ig["id"]}]})
    assert r.status_code == 422 and "only reachable on this computer" in r.json()["error"]["message"]

    monkeypatch.setattr(get_settings(), "public_base_url", "https://news.example.org")
    r = admin.post(f"/articles/{art['id']}/publish-many", json={"targets": [{"destination_id": ig["id"]}]}).json()
    job = r["jobs"][0]
    assert job["status"] == "succeeded", job["last_error"]
    assert job["remote_url"] == "https://www.instagram.com/p/ABC/"
    sent = meta.published[0]
    assert sent["image_url"].startswith("https://news.example.org/media/") and "Garment factories reopen" in sent["caption"]

    # Instagram has no drafts, and apps cannot delete posts.
    art2 = _photo_article(admin)
    r = admin.post(f"/articles/{art2['id']}/publish-many", json={"mode": "draft", "targets": [{"destination_id": ig["id"]}]})
    assert r.status_code == 422 and "no drafts" in r.json()["error"]["message"]
    un = admin.post(f"/articles/{art['id']}/unpublish", json={"destination_id": ig["id"]})
    body = un.json()
    assert "Instagram does not let apps delete posts" in (body.get("job") or {}).get("last_error", "") or \
        "delete" in str(body).lower()


def test_instagram_without_a_linked_account_is_explained(client, meta):
    admin = setup_admin(client)
    save_meta_app(admin)
    meta.pages[0].pop("instagram_business_account")
    d = admin.post("/publishing/destinations", json={"name": "Instagram", "kind": "instagram"}).json()
    start = admin.post(f"/publishing/destinations/{d['id']}/connect").json()
    r = callback(client, "", state=state_of(start["authorize_url"]), code="good-code")
    assert "connect_error=" in r.headers["location"] and "Professional" in r.headers["location"].replace("+", " ")


# --- WordPress behind an admin login -----------------------------------------------------------------

WP = "https://wp.example.com"


class FakeWordPress:
    def __init__(self, web):
        self.app_passwords: list[str] = []
        self.revoked = False
        self.posts: list[dict] = []
        self.captcha = False
        web.handler = self.handle

    def handle(self, request: httpx.Request) -> httpx.Response:
        request.read()
        url = str(request.url)
        path = url[len(WP):]
        cookie = request.headers.get("cookie", "")
        if path == "/wp-json/?_fields=namespaces":
            return httpx.Response(200, json={"namespaces": ["oembed/1.0", "wp/v2"]})
        if path == "/wp-login.php" and request.method == "POST":
            f = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            assert "wordpress_test_cookie" in cookie
            if self.captcha:
                return httpx.Response(200, text='<div class="g-recaptcha"></div>')
            if f["log"] == "editor@example.com" and f["pwd"] == "right-password":
                return httpx.Response(302, headers={"location": f"{WP}/wp-admin/",
                                                    "set-cookie": "wordpress_logged_in_abc=editor; path=/"})
            return httpx.Response(200, text='<div id="login_error">Incorrect password</div>')
        if path == "/wp-admin/admin-ajax.php?action=rest-nonce":
            return httpx.Response(200, text="a1b2c3d4e5") if "wordpress_logged_in" in cookie else httpx.Response(400, text="0")
        if path == "/wp-json/wp/v2/users/me?context=edit":
            assert request.headers.get("x-wp-nonce") == "a1b2c3d4e5"
            return httpx.Response(200, json={"username": "editor1", "name": "Editor One",
                                             "capabilities": {"publish_posts": True}})
        if path == "/wp-json/wp/v2/users/me/application-passwords":
            pw = f"app pass {len(self.app_passwords) + 1}"
            self.app_passwords.append(pw)
            self.revoked = False
            return httpx.Response(201, json={"password": pw, "uuid": "u1"})
        if path.startswith("/wp-json/wp/v2/"):
            auth = base64.b64decode(request.headers["authorization"].split()[1]).decode()
            if self.revoked or auth != f"editor1:{self.app_passwords[-1]}":
                return httpx.Response(401, json={"code": "rest_not_logged_in", "message": "bad auth"})
            if path == "/wp-json/wp/v2/posts":
                import json as _json

                post = _json.loads(request.content)
                self.posts.append(post)
                return httpx.Response(201, json={"id": len(self.posts), "link": f"{WP}/?p={len(self.posts)}",
                                                 "status": post["status"]})
            if path.startswith("/wp-json/wp/v2/users/me"):
                return httpx.Response(200, json={"name": "Editor One", "slug": "editor1"})
        return httpx.Response(404, text="not found")


def test_wordpress_found_behind_the_admin_login(client, web):
    wp = FakeWordPress(web)
    admin = setup_admin(client)
    bad = admin.post("/publishing/destinations", json={"name": "News site", "kind": "website",
                                                       "base_url": f"{WP}/wp-login.php", "username": "editor@example.com",
                                                       "secret": "wrong"})
    assert bad.status_code == 422 and "did not accept" in bad.json()["error"]["message"]

    r = admin.post("/publishing/destinations", json={"name": "News site", "kind": "website",
                                                     "base_url": f"{WP}/wp-login.php", "username": "editor@example.com",
                                                     "secret": "right-password"})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["website"]["platform"] == "wordpress" and d["website"]["wp_user"] == "editor1"
    assert d["has_secret"] is True and "right-password" not in r.text and "app pass" not in r.text
    assert d["last_check_status"].startswith("OK: WordPress found")

    art = approved_article(admin)
    job = admin.post(f"/articles/{art['id']}/publish-many", json={"targets": [{"destination_id": d["id"]}]}).json()["jobs"][0]
    assert job["status"] == "succeeded", job["last_error"]
    assert wp.posts[0]["title"] == "Garment factories reopen" and wp.posts[0]["status"] == "publish"

    # Someone revokes NEWSWIRE's application password: the stored login makes a new one.
    wp.revoked = True
    art2 = approved_article(admin)
    job = admin.post(f"/articles/{art2['id']}/publish-many", json={"targets": [{"destination_id": d["id"]}]}).json()["jobs"][0]
    assert job["status"] == "succeeded", job["last_error"]
    assert len(wp.app_passwords) == 2 and len(wp.posts) == 2


def test_wordpress_login_with_captcha_is_explained(client, web):
    wp = FakeWordPress(web)
    wp.captcha = True
    admin = setup_admin(client)
    r = admin.post("/publishing/destinations", json={"name": "News site", "kind": "website",
                                                     "base_url": f"{WP}/wp-admin/", "username": "editor@example.com",
                                                     "secret": "right-password"})
    assert r.status_code == 422 and "captcha" in r.json()["error"]["message"]


# --- any other site: the robot browser ---------------------------------------------------------------


def _chromium_ready() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
        return True
    except Exception:  # noqa: BLE001
        return False


needs_chromium = pytest.mark.skipif(not _chromium_ready(), reason="Chromium for Playwright is not installed")

LOGIN_PAGE = """<!doctype html><title>Admin login</title>
<h1>Newsfield admin</h1>{error}
<form method="post" action="/admin/login">
  <label>Email <input type="email" name="email"></label>
  <label>Password <input type="password" name="password"></label>
  <button type="submit">Sign in</button>
</form>"""

NEW_POST_PAGE = """<!doctype html><title>Add news</title>
<nav><a href="/admin">Dashboard</a></nav>{error}
<form id="f" method="post" action="/admin/news" enctype="multipart/form-data">
  <p><label>Headline <input id="headline" name="headline"></label></p>
  <div id="toolbar"><button type="button">B</button></div>
  <div id="editor" contenteditable="true" style="min-height:80px;border:1px solid #999"><p><br></p></div>
  <input type="hidden" name="body" id="body">
  <p><label for="photo" class="btn">Choose photo</label><input type="file" id="photo" name="photo" style="display:none"></p>
  <p><input name="summary" placeholder="Short description"></p>
  <button type="submit" class="primary">Publish news</button>
</form>
<script>document.getElementById('f').addEventListener('submit', () => {{
  document.getElementById('body').value = document.getElementById('editor').innerHTML;
}});</script>"""


class AdminSite:
    """A tiny custom admin panel: cookie login, an 'Add news' form with a rich-text box."""

    def __init__(self):
        self.posts: list[dict] = []
        self.sessions: set[str] = set()
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # quiet
                pass

            def _send(self, code, body="", headers=None):
                data = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _signed_in(self):
                m = re.search(r"sid=(\w+)", self.headers.get("Cookie", ""))
                return bool(m and m.group(1) in site.sessions)

            def do_GET(self):  # noqa: N802
                path = urlsplit(self.path).path
                if path == "/admin/login":
                    return self._send(200, LOGIN_PAGE.format(error=""))
                if path in ("/admin", "/admin/news/new") and not self._signed_in():
                    return self._send(303, headers={"Location": "/admin/login"})
                if path == "/admin":
                    return self._send(200, '<h1>Dashboard</h1><a href="/admin/news/new">Add news</a>')
                if path == "/admin/news/new":
                    return self._send(200, NEW_POST_PAGE.format(error=""))
                if path.startswith("/admin/news/"):
                    n = path.rsplit("/", 1)[1]
                    return self._send(200, f'<p class="ok">Saved.</p><a href="/news/{n}">View post</a>')
                if path == "/wp-json/":
                    return self._send(404, "no")
                return self._send(404, "not found")

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                path = urlsplit(self.path).path
                if path == "/admin/login":
                    f = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
                    if f.get("email") == "admin@newsfield.test" and f.get("password") == "s3cret-pass":
                        sid = f"s{len(site.sessions) + 1}{int(time.time() * 1000)}"
                        site.sessions.add(sid)
                        return self._send(303, headers={"Location": "/admin", "Set-Cookie": f"sid={sid}; Path=/"})
                    return self._send(200, LOGIN_PAGE.format(error='<p class="error">Wrong email or password</p>'))
                if path == "/admin/news":
                    if not self._signed_in():
                        return self._send(303, headers={"Location": "/admin/login"})
                    msg = BytesParser(policy=email_policy).parsebytes(
                        f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode() + raw)
                    fields = {}
                    for part in msg.iter_parts():
                        name = part.get_param("name", header="content-disposition")
                        fields[name] = part.get_content() if part.get_filename() is None else len(part.get_payload(decode=True))
                    if not (fields.get("headline") or "").strip():
                        return self._send(200, NEW_POST_PAGE.format(error='<p class="error">Headline is required</p>'))
                    site.posts.append(fields)
                    return self._send(303, headers={"Location": f"/admin/news/{len(site.posts)}"})
                return self._send(404, "not found")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture()
def admin_site(monkeypatch):
    monkeypatch.setattr(get_settings(), "allow_private_network", True)
    site = AdminSite()
    yield site
    site.close()


def teach_like_a_person(site: AdminSite):
    """What an administrator does in the setup window, step by step."""

    def bar(page, label):
        page.locator("#__nw_teach_bar").get_by_role("button", name=label).click()

    def act(page, key):
        page.wait_for_timeout(300)
        if key == "page":
            page.goto(f"{site.base}/admin")
            page.get_by_role("link", name="Add news").click()
            page.wait_for_load_state()
            bar(page, "This is the page")
        elif key == "title":
            page.click("#headline")
        elif key == "body":
            page.click("#editor p")  # a paragraph inside the editor: the whole editor is meant
        elif key == "image":
            page.click("label[for=photo]")  # the styled label: the file input behind it is meant
        elif key == "excerpt":
            page.click("input[name=summary]")
        elif key in ("category", "tags"):
            bar(page, "Skip")
        elif key == "submit":
            page.get_by_role("button", name="Publish news").click()  # recorded, not pressed

    return act


def publish_in_background(admin, article_id, dest_id) -> dict:
    """Robot-browser jobs run after the response (the test client waits for them)."""
    r = admin.post(f"/articles/{article_id}/publish-many", json={"targets": [{"destination_id": dest_id}]}).json()
    assert r["in_progress"], r
    return admin.get("/publishing/jobs", params={"article_id": article_id}).json()["items"][0]


def wait_for_teaching(admin, dest_id, seconds=60):
    deadline = time.time() + seconds
    while time.time() < deadline:
        st = admin.get(f"/publishing/destinations/{dest_id}/teach").json()
        if st["state"] in ("done", "failed", "cancelled"):
            return st
        time.sleep(0.3)
    raise AssertionError(f"teaching did not finish: {st}")


@needs_chromium
def test_custom_admin_panel_is_taught_once_then_publishes(client, admin_site, monkeypatch):
    admin = setup_admin(client)
    bad = admin.post("/publishing/destinations", json={"name": "Newsfield", "kind": "website",
                                                       "base_url": f"{admin_site.base}/admin/login",
                                                       "username": "admin@newsfield.test", "secret": "s3cret-pass"})
    assert bad.status_code == 201, bad.text
    d = bad.json()
    assert d["website"]["platform"] == "custom" and d["website"]["taught"] is False
    assert "Teach" in d["last_check_status"]

    art = approved_article(admin)
    r = admin.post(f"/articles/{art['id']}/publish-many", json={"targets": [{"destination_id": d["id"]}]})
    assert r.status_code == 422 and "Teach" in r.json()["error"]["message"]

    monkeypatch.setattr(website, "start_teaching",
                        partial(website.start_teaching, headless=True, driver=teach_like_a_person(admin_site)))
    st = admin.post(f"/publishing/destinations/{d['id']}/teach").json()
    assert st["state"] in ("starting", "running")
    st = wait_for_teaching(admin, d["id"])
    assert st["state"] == "done", st
    assert admin_site.posts == []  # the Publish button was shown, not pressed

    d = next(x for x in admin.get("/publishing/destinations").json() if x["id"] == d["id"])
    assert d["website"]["taught"] is True and d["website"]["new_post_url"].endswith("/admin/news/new")
    assert d["website"]["fields"] == ["body", "excerpt", "image", "title"]
    assert "s3cret" not in str(d) and "sid=" not in str(d)

    job = publish_in_background(admin, art["id"], d["id"])
    assert job["status"] == "succeeded", job["last_error"]
    post = admin_site.posts[0]
    assert post["headline"] == "Garment factories reopen"
    assert "<p>" in post["body"] and len(post["body"]) > 200  # the article's HTML went into the rich-text box
    assert post["summary"] == "Workers returned on Monday."
    assert job["remote_url"].endswith("/news/1")

    # The site signs everyone out: the stored password signs in again. This one has a photo,
    # which goes through the styled "Choose photo" button's hidden file input.
    admin_site.sessions.clear()
    art2 = _photo_article(admin)
    job = publish_in_background(admin, art2["id"], d["id"])
    assert job["status"] == "succeeded", job["last_error"]
    assert len(admin_site.posts) == 2 and admin_site.posts[1]["photo"] > 1000

    test = admin.post(f"/publishing/destinations/{d['id']}/test").json()
    assert test["ok"] is True and "title" in test["message"] and "body" in test["message"], test

    # Changing a published post is not possible through a post form: said plainly.
    un = admin.post(f"/articles/{art['id']}/unpublish", json={"destination_id": d["id"]})
    assert "admin panel" in un.text


@needs_chromium
def test_wrong_admin_password_is_reported_by_the_robot(client, admin_site, monkeypatch):
    admin = setup_admin(client)
    d = admin.post("/publishing/destinations", json={"name": "Newsfield", "kind": "website",
                                                     "base_url": f"{admin_site.base}/admin/login",
                                                     "username": "admin@newsfield.test", "secret": "wrong"}).json()
    test = admin.post(f"/publishing/destinations/{d['id']}/test").json()
    assert test["ok"] is False and "did not accept the admin email and password" in test["message"]
    assert "Wrong email or password" in test["message"]
