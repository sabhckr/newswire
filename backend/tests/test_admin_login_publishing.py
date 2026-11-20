"""Publishing with a website's own admin login, the shared Meta app, and Instagram.

* WordPress behind an admin login: an in-process fake WordPress (login form, REST nonce,
  application passwords, posts).
* Any other site: NEWSWIRE's robot engine (app/robot) drives a real browser from this computer
  against a small admin panel served on 127.0.0.1, is taught the post form once, and publishes
  through it. Skipped if no Chromium-family browser is installed.
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


def _browser_ready() -> bool:
    from app import robot

    try:
        robot.find_browser(headless=True)
        return True
    except robot.BrowserNotFound:
        return False


needs_chromium = pytest.mark.skipif(not _browser_ready(), reason="No Chromium-family browser is installed")

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


# A busier admin panel: the editor lives in a frame, the photo goes through a styled button that
# opens a file window, Publish is greyed out while the photo "uploads", asks "are you sure?", saves
# in the background and stays on the same address.
FANCY_PAGE = """<!doctype html><title>Compose</title>
<header style="position:sticky;top:0;height:50px;background:#eee">Admin</header>
<div id="msg"></div>
<form id="f">
  <p><input name="title" id="t" placeholder="Post title"></p>
  <iframe id="ed" src="/admin/editor" style="width:500px;height:150px"></iframe>
  <p><select name="cat" id="cat"><option value="">--</option><option value="3">Bangladesh</option>
     <option value="4">{category}</option></select>
     <input name="tags" id="tags" placeholder="Tags"></p>
  <input type="file" name="photo" id="file" style="display:none">
  <button type="button" id="up">Upload photo…</button>
  <button type="submit" id="save">Publish</button>
</form>
<script>
const f = document.getElementById('f'), file = document.getElementById('file'), save = document.getElementById('save');
document.getElementById('up').addEventListener('click', () => file.click());
file.addEventListener('change', () => {{ save.disabled = true; setTimeout(() => {{ save.disabled = false; }}, 900); }});
f.addEventListener('submit', async (e) => {{
  e.preventDefault();
  if (!confirm('Publish this post?')) return;
  const data = new FormData(f);
  data.set('body', document.getElementById('ed').contentDocument.body.innerHTML);
  const r = await fetch('/admin/fancy', {{method: 'POST', body: data}});
  const text = await r.text();
  document.getElementById('msg').innerHTML = r.ok
    ? '<p class="ok">Saved</p><a href="/news/' + text + '">View post</a>'
    : '<div class="alert-danger">' + text + '</div>';
}});
</script>"""
EDITOR_FRAME = """<!doctype html><title>Editor</title><body contenteditable="true"><p><br></p></body>"""


class AdminSite:
    """A tiny custom admin panel: cookie login, an 'Add news' form with a rich-text box."""

    def __init__(self):
        self.posts: list[dict] = []
        self.sessions: set[str] = set()
        self.category = "Business"
        self.reject: str | None = None
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
                if path in ("/admin", "/admin/news/new", "/admin/fancy/new") and not self._signed_in():
                    return self._send(303, headers={"Location": "/admin/login"})
                if path == "/admin/fancy/new":
                    return self._send(200, FANCY_PAGE.format(category=site.category))
                if path == "/admin/editor":
                    return self._send(200, EDITOR_FRAME)
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
                if path == "/admin/fancy":
                    if not self._signed_in():
                        return self._send(401, "Please sign in")
                    if site.reject:
                        return self._send(422, site.reject)
                    msg = BytesParser(policy=email_policy).parsebytes(
                        f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode() + raw)
                    fields = {}
                    for part in msg.iter_parts():
                        name = part.get_param("name", header="content-disposition")
                        data = part.get_payload(decode=True) or b""
                        fields[name] = data.decode("utf-8") if part.get_filename() is None else len(data)
                    site.posts.append(fields)
                    return self._send(200, str(len(site.posts)))
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
        assert page.wait_for(lambda: page.find("#__nw_teach_bar") is not None, 10)
        assert page.wait_for(lambda: page.find_button(label, root=page.find("#__nw_teach_bar")) is not None, 10), label
        page.find_button(label, root=page.find("#__nw_teach_bar")).click()

    def act(page, key):
        time.sleep(0.6)  # a person reads the instruction first
        if key == "page":
            page.goto(f"{site.base}/admin")
            before = page.url
            page.find_link("Add news").click()
            page.wait_for_url_change(before, 10)
            page.settle(10)
            bar(page, "This is the page")
        elif key == "title":
            page.find("#headline").click()
        elif key == "body":
            page.find("#editor p").click()  # a paragraph inside the editor: the whole editor is meant
        elif key == "image":
            page.find("label[for=photo]").click()  # the styled label: the file input behind it is meant
        elif key == "excerpt":
            page.find("input[name=summary]").click()
        elif key in ("category", "tags"):
            bar(page, "Skip")
        elif key == "submit":
            page.find_button("Publish news").click()  # recorded, not pressed

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


def teach_the_fancy_form(site: AdminSite):
    def bar(page, label):
        assert page.wait_for(lambda: page.find_button(label, root=page.find("#__nw_teach_bar")) is not None, 10), label
        page.find_button(label, root=page.find("#__nw_teach_bar")).click()

    def act(page, key):
        time.sleep(0.6)
        if key == "page":
            page.goto(f"{site.base}/admin/fancy/new")
            page.settle(10)
            bar(page, "This is the page")
        elif key == "title":
            page.find("#t").click()
            page.find("#t").click()          # a nervous double-click must not answer the next question
        elif key == "body":
            page.find("#ed").content_frame().body().click()   # inside the editor's own frame
        elif key == "image":
            page.find("#up").click()
        elif key == "excerpt":
            bar(page, "Skip")
        elif key == "category":
            page.find("#cat").click()
        elif key == "tags":
            page.find("#tags").click()
        elif key == "submit":
            page.find("#save").click()

    return act


def add_website(admin, site, password="s3cret-pass") -> dict:
    r = admin.post("/publishing/destinations", json={"name": "Newsfield", "kind": "website",
                                                     "base_url": f"{site.base}/admin/login",
                                                     "username": "admin@newsfield.test", "secret": password})
    assert r.status_code == 201, r.text
    return r.json()


@needs_chromium
def test_editor_in_a_frame_upload_button_lists_and_background_saving(client, admin_site, monkeypatch):
    admin = setup_admin(client)
    cat = admin.post("/categories", json={"name": "Economy desk"})
    assert cat.status_code == 201, cat.text
    admin_site.category = "Economy desk"
    d = add_website(admin, admin_site)

    monkeypatch.setattr(website, "start_teaching",
                        partial(website.start_teaching, headless=True, driver=teach_the_fancy_form(admin_site)))
    admin.post(f"/publishing/destinations/{d['id']}/teach")
    st = wait_for_teaching(admin, d["id"])
    assert st["state"] == "done", st
    assert admin_site.posts == []
    d = next(x for x in admin.get("/publishing/destinations").json() if x["id"] == d["id"])
    assert d["website"]["fields"] == ["body", "category", "image", "tags", "title"]
    assert d["website"]["new_post_url"].endswith("/admin/fancy/new")

    buf = io.BytesIO()
    Image.new("RGB", (1080, 1080), (20, 60, 140)).save(buf, "JPEG")
    img = admin.post("/images/upload", files={"file": ("photo.jpg", buf.getvalue(), "image/jpeg")},
                     data={"license_code": "cc0", "title": "Factory"}).json()
    admin.post(f"/images/{img['id']}/approve")
    art = approved_article(admin, featured_image_id=img["id"], category_id=cat.json()["id"],
                           tags=["garments", "ঢাকা"])
    job = publish_in_background(admin, art["id"], d["id"])
    assert job["status"] == "succeeded", job["last_error"]
    post = admin_site.posts[0]
    assert post["title"] == "Garment factories reopen"
    assert "<p>" in post["body"] and len(post["body"]) > 200       # pasted into the editor's frame
    assert post["cat"] == "4"                                        # the list entry named like the category
    assert set(post["tags"].split(", ")) == {"garments", "ঢাকা"}
    assert post["photo"] > 1000                                      # through the button's file window
    assert job["remote_url"].endswith("/news/1")                     # the link shown after saving in the background

    # The website refuses a post: its own words are passed on, with a screenshot, and it is not retried.
    admin_site.reject = "A post with this title already exists"
    art2 = approved_article(admin)
    job = publish_in_background(admin, art2["id"], d["id"])
    assert job["status"] != "succeeded"
    assert "did not accept the post: A post with this title already exists" in job["last_error"]
    assert "screenshot: storage/logs/robot/" in job["last_error"]
    assert len(admin_site.posts) == 1
    shots = list((get_settings().logs_dir / "robot").glob("*.png"))
    assert shots and shots[0].read_bytes()[:4] == b"\x89PNG"


@needs_chromium
def test_closing_the_setup_window_cancels_teaching(client, admin_site, monkeypatch):
    admin = setup_admin(client)
    d = add_website(admin, admin_site)

    def close_the_window(page, key):
        time.sleep(0.3)
        page.browser._conn.send("Target.closeTarget", {"targetId": page.target_id})

    monkeypatch.setattr(website, "start_teaching",
                        partial(website.start_teaching, headless=True, driver=close_the_window))
    admin.post(f"/publishing/destinations/{d['id']}/teach")
    st = wait_for_teaching(admin, d["id"])
    assert st["state"] == "cancelled" and st["error"] is None, st
    assert "window was closed" in st["message"]

    # Cancel from NEWSWIRE while the window waits for the person.
    monkeypatch.setattr(website, "start_teaching",
                        partial(website.start_teaching, headless=True, driver=lambda page, key: None))
    assert admin.post(f"/publishing/destinations/{d['id']}/teach").json()["state"] in ("starting", "running")
    deadline = time.time() + 30
    while admin.get(f"/publishing/destinations/{d['id']}/teach").json()["mode"] != "page":
        assert time.time() < deadline
        time.sleep(0.2)
    admin.delete(f"/publishing/destinations/{d['id']}/teach")
    st = wait_for_teaching(admin, d["id"])
    assert st["state"] == "cancelled" and "cancelled" in st["message"], st
    d = next(x for x in admin.get("/publishing/destinations").json() if x["id"] == d["id"])
    assert d["website"]["taught"] is False


@needs_chromium
def test_no_browser_on_the_computer_is_explained(client, admin_site, monkeypatch):
    admin = setup_admin(client)
    d = add_website(admin, admin_site)
    monkeypatch.setattr("app.robot.chrome.browser_candidates", lambda headless=True: [])
    test = admin.post(f"/publishing/destinations/{d['id']}/test").json()
    assert test["ok"] is False and "Install Google Chrome" in test["message"], test
    admin.post(f"/publishing/destinations/{d['id']}/teach")
    st = wait_for_teaching(admin, d["id"])
    assert st["state"] == "failed" and "Install Google Chrome" in st["error"], st


@needs_chromium
def test_website_that_is_down_is_reported_and_retried(client, admin_site, monkeypatch):
    admin = setup_admin(client)
    d = add_website(admin, admin_site)
    admin_site.close()
    admin_site.server.server_close()   # nothing listens on the port any more
    test = admin.post(f"/publishing/destinations/{d['id']}/test").json()
    assert test["ok"] is False and "could not be opened" in test["message"] and "refused" in test["message"], test
