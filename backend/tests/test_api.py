"""API integration tests: auth, CSRF, RBAC and the full editorial workflow end to end.

External services (feeds, image repositories, WordPress, webhooks) are replaced by an in-process
httpx.MockTransport; nothing here touches the network.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.services.bootstrap import bootstrap
from tests.conftest import rss

PASSWORD = "correct horse battery"
FEED = "https://news.example.com/world.rss"
LONG = ("<p>Garment factories across Bangladesh reopened on Monday after weeks of protests over wages, "
        "with workers returning to production lines in Dhaka and Chittagong as negotiations continue.</p>")


class Web:
    """Exact-URL routes for the mock transport. A route is (status, headers, body) or a callable."""

    def __init__(self):
        self.routes: dict[str, object] = {}
        self.requests: list[httpx.Request] = []

    def add(self, url, body=b"", status=200, headers=None):
        self.routes[url] = (status, headers or {"content-type": "application/rss+xml"}, body.encode() if isinstance(body, str) else body)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get(str(request.url))
        if route is None:
            return httpx.Response(404, text="no route")
        if callable(route):
            return route(request)
        status, headers, body = route
        return httpx.Response(status, headers=headers, content=body)


@pytest.fixture()
def web(monkeypatch):
    w = Web()

    def fake_client(**_kw):
        return httpx.Client(transport=httpx.MockTransport(w.handler), follow_redirects=False)

    import app.api.publishing
    import app.collectors.ingest
    import app.images.providers
    import app.images.service
    import app.publishing.service

    for mod in (app.collectors.ingest, app.publishing.service, app.images.service, app.images.providers, app.api.publishing):
        monkeypatch.setattr(mod, "make_client", fake_client)
    return w


@pytest.fixture()
def client(db):
    from app.main import create_app

    bootstrap(db)
    db.commit()
    with TestClient(create_app(run_startup=False)) as c:
        yield c


class Session_:
    """A signed-in TestClient that sends the CSRF header on unsafe requests."""

    def __init__(self, client: TestClient, csrf: str):
        self.c = client
        self.csrf = csrf

    def __getattr__(self, method):
        def call(url, **kw):
            headers = kw.pop("headers", {}) or {}
            if method.upper() not in ("GET", "HEAD"):
                headers.setdefault("X-CSRF-Token", self.csrf)
            return getattr(self.c, method)(f"/api/v1{url}", headers=headers, **kw)

        return call


def setup_admin(client) -> Session_:
    r = client.post("/api/v1/auth/setup", json={"email": "admin@example.com", "display_name": "Admin", "password": PASSWORD})
    assert r.status_code == 200, r.text
    return Session_(client, r.json()["csrf_token"])


def login(client, email, password=PASSWORD) -> Session_:
    client.cookies.clear()
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return Session_(client, r.json()["csrf_token"])


def make_user(admin: Session_, email: str, role: str):
    r = admin.post("/users", json={"email": email, "display_name": role.title(), "password": PASSWORD, "roles": [role]})
    assert r.status_code == 201, r.text


def jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (800, 500), (30, 90, 160)).save(buf, "JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------------------------------------


def test_health_and_security_headers(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert client.get("/api/openapi.json").status_code == 200


def test_setup_login_logout_and_csrf(client):
    assert client.get("/api/v1/auth/setup-status").json() == {"needs_setup": True}
    weak = client.post("/api/v1/auth/setup", json={"email": "a@example.com", "display_name": "A", "password": "aaaaaaaaaa"})
    assert weak.status_code == 422
    admin = setup_admin(client)
    assert client.get("/api/v1/auth/setup-status").json() == {"needs_setup": False}
    again = client.post("/api/v1/auth/setup", json={"email": "b@example.com", "display_name": "B", "password": PASSWORD})
    assert again.status_code == 409

    me = admin.get("/auth/me").json()
    assert me["user"]["email"] == "admin@example.com" and "system:manage" in me["permissions"]

    # Unsafe request without the CSRF header is refused even with a valid session.
    no_csrf = client.post("/api/v1/categories", json={"name": "Maritime"})
    assert no_csrf.status_code == 403 and no_csrf.json()["error"]["code"] == "csrf_failed"
    assert admin.post("/categories", json={"name": "Maritime"}).status_code == 201

    assert admin.post("/auth/logout").status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401

    bad = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "wrong password!"})
    assert bad.status_code == 401
    unknown = client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong password!"})
    assert unknown.status_code == 401 and unknown.json()["error"]["message"] == bad.json()["error"]["message"]
    login(client, "admin@example.com")


def test_account_lockout(client):
    setup_admin(client)
    client.cookies.clear()
    for _ in range(8):
        client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "not the password"})
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": PASSWORD})
    assert r.status_code == 429 and r.json()["error"]["code"] == "account_locked"


def test_rbac(client, web):
    admin = setup_admin(client)
    make_user(admin, "viewer@example.com", "viewer")
    make_user(admin, "researcher@example.com", "researcher")

    viewer = login(client, "viewer@example.com")
    assert viewer.get("/sources").status_code == 200
    denied = viewer.post("/sources", json={"name": "X", "publisher": "X", "feed_url": FEED})
    assert denied.status_code == 403
    assert viewer.get("/system/logs").status_code == 403
    assert viewer.get("/users").status_code == 403

    researcher = login(client, "researcher@example.com")
    a = researcher.post("/articles", json={"headline": "Draft by a researcher", "body_html": "<p>Text</p>"})
    assert a.status_code == 201
    art = a.json()
    assert set(art["transitions"]) == {"in_review", "archived"}
    assert researcher.post(f"/articles/{art['id']}/transition", json={"target": "in_review"}).status_code == 200
    # A researcher cannot approve.
    r = researcher.post(f"/articles/{art['id']}/transition", json={"target": "approved"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_transition"
    assert researcher.put("/settings/publishing", json={"auto_publish_policy": True}).status_code == 403

    # The last administrator cannot be demoted.
    admin = login(client, "admin@example.com")
    me = admin.get("/auth/me").json()["user"]
    assert admin.patch(f"/users/{me['id']}", json={"roles": ["editor"]}).status_code in (400, 409)


def test_full_editorial_workflow(client, web):
    """Source → ingestion → assignment → matches → research → image → article → approval → publish → audit."""
    admin = setup_admin(client)
    web.add(FEED, rss([
        {"title": "DHAKA, Nov 17 (Reuters) - Bangladesh garment factories reopen after wage protests",
         "link": "https://news.example.com/a/1", "description": "Garment workers in Dhaka returned on Monday after weeks of protests.",
         "pubDate": "Mon, 17 Nov 2026 08:00:00 GMT"},
        {"title": "Bangladesh garment factories reopen after protests over wages",
         "link": "https://news.example.com/a/2", "description": "Factories in Dhaka and Chittagong reopened.",
         "pubDate": "Mon, 17 Nov 2026 09:00:00 GMT"},
        {"title": "Local football club wins the cup", "link": "https://news.example.com/a/3",
         "description": "A sports story about the final.", "pubDate": "Mon, 17 Nov 2026 07:00:00 GMT"},
    ]))

    # 1. Source manager: test, create, refresh.
    probe = admin.post("/sources/test", json={"feed_url": FEED, "source_type": "rss"})
    assert probe.status_code == 200 and probe.json()["ok"] is True, probe.text
    src = admin.post("/sources", json={"name": "Example World", "publisher": "Example News", "feed_url": FEED,
                                       "country_code": "gb", "language": "en"})
    assert src.status_code == 201, src.text
    sid = src.json()["id"]
    dup = admin.post("/sources", json={"name": "Again", "publisher": "Example News", "feed_url": FEED})
    assert dup.status_code == 409
    ref = admin.post(f"/sources/{sid}/refresh")
    assert ref.status_code == 200, ref.text
    assert ref.json()["log"]["status"] == "success" and ref.json()["log"]["items_new"] == 3

    news = admin.get("/news", params={"q": "garment"}).json()
    assert news["total"] == 2
    assert "BD" in news["items"][0]["countries"]

    # 2. Assignment with keywords and geography → matches with explanations.
    bd = admin.get("/geography/entities", params={"q": "Bangladesh", "kind": "country"}).json()
    bd_id = (bd["items"] if isinstance(bd, dict) else bd)[0]["id"]
    asg = admin.post("/assignments", json={
        "headline": "Bangladesh garment sector after the wage protests",
        "keywords": [{"term": "garment", "kind": "primary"}, {"term": "factories", "kind": "secondary"},
                     {"term": "football", "kind": "excluded"}],
        "geography_ids": [bd_id], "priority": "high",
    })
    assert asg.status_code == 201, asg.text
    aid = asg.json()["id"]
    matches = admin.get(f"/assignments/{aid}/matches").json()
    assert matches["total"] == 2
    top = matches["items"][0]
    assert top["explanation"].startswith("Matched because") and top["score"] >= 25
    assert all("football" not in m["news_item"]["title"].lower() for m in matches["items"])
    for m in matches["items"]:
        assert admin.patch(f"/assignments/matches/{m['id']}", json={"review_status": "verified"}).status_code == 200

    # 3. Clusters: the two near-identical reports were clustered.
    clusters = admin.get("/clusters").json()
    assert any(c["item_count"] == 2 for c in clusters["items"])

    # 4. Research workspace.
    ws = admin.get(f"/research/{aid}").json()
    assert ws["assignment"]["id"] == aid if "assignment" in ws else True
    assert admin.patch(f"/research/{aid}", json={"editorial_notes": "Call the BGMEA spokesperson", "research_status": "in_progress"}).status_code == 200

    # 5. Image: save an Openverse candidate, approve (downloads through the mock), licence recorded.
    img_url = "https://images.example.org/factory.jpg"
    web.add(img_url, jpeg_bytes(), headers={"content-type": "image/jpeg"})
    saved = admin.post("/images", json={"assignment_id": aid, "query": "garment factory", "candidate": {
        "provider": "openverse", "provider_id": "ov-123", "title": "Garment factory floor", "creator": "J. Photographer",
        "source_page_url": "https://images.example.org/page", "original_url": img_url, "license_code": "by",
        "license_version": "4.0", "attribution_text": "“Garment factory floor” by J. Photographer, CC BY 4.0",
    }})
    assert saved.status_code == 201, saved.text
    image = saved.json()
    assert image["approval_status"] == "pending"
    approved = admin.post(f"/images/{image['id']}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["local_url"] and approved.json()["local_thumbnail_url"]
    media = client.get(approved.json()["local_url"].replace("http://localhost:3000", ""))
    assert media.status_code == 200 and media.headers["content-type"] == "image/jpeg"

    # Unknown licence cannot be approved.
    unknown = admin.post("/images", json={"candidate": {"provider": "wikimedia", "provider_id": "File:X.jpg",
                                                         "original_url": img_url, "license_code": "unknown"}}).json()
    r = admin.post(f"/images/{unknown['id']}/approve")
    assert r.status_code == 422 and "unknown licence" in r.json()["error"]["message"]

    # 6. Article from the assignment: template contains editor markers that block publication.
    draft = admin.post(f"/research/{aid}/article")
    assert draft.status_code == 201, draft.text
    art = draft.json()
    art_id = art["id"]
    assert art["featured_image_id"] == image["id"]
    assert len(art["sources"]) == 2
    assert any(c["level"] == "error" and "EDITOR" in c["message"] for c in art["checks"])

    # Version check: a stale save is refused.
    stale = admin.put(f"/articles/{art_id}", json={"headline": "Stale", "version": art["version"] - 1})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "version_conflict"
    body = LONG + "<script>alert(1)</script>"
    saved_art = admin.put(f"/articles/{art_id}", json={
        "headline": "Bangladesh garment factories reopen", "body_html": body, "excerpt": "Factories reopen after protests.",
        "tags": ["Bangladesh", "Labour"], "featured_image_caption": "A garment factory", "version": art["version"],
    })
    assert saved_art.status_code == 200, saved_art.text
    art = saved_art.json()
    assert "<script" not in art["body_html"]
    assert not [c for c in art["checks"] if c["level"] == "error"], art["checks"]

    dest = next(d for d in admin.get("/publishing/destinations").json() if d["kind"] == "builtin")
    # Not approved yet: publishing is refused.
    early = admin.post(f"/articles/{art_id}/publish", json={"destination_id": dest["id"]})
    assert early.status_code == 409 and early.json()["error"]["code"] == "not_approved"

    assert admin.post(f"/articles/{art_id}/transition", json={"target": "in_review"}).status_code == 200
    appr = admin.post(f"/articles/{art_id}/transition", json={"target": "approved"})
    assert appr.status_code == 200 and appr.json()["status"] == "approved"
    # Auto-publish is off by default: approval alone publishes nothing.
    assert admin.get("/publishing/jobs").json()["total"] == 0

    # 7. Publish to the built-in site; idempotent repeat.
    pub = admin.post(f"/articles/{art_id}/publish", json={"destination_id": dest["id"]})
    assert pub.status_code == 200, pub.text
    assert pub.json()["job"]["status"] == "succeeded" and pub.json()["article"]["status"] == "published"
    again = admin.post(f"/articles/{art_id}/publish", json={"destination_id": dest["id"]})
    assert again.json()["duplicate_request"] is True
    assert admin.get("/publishing/jobs").json()["total"] == 1

    client.cookies.clear()
    public = client.get("/api/v1/public/articles").json()
    assert public["total"] == 1
    slug = public["items"][0]["slug"]
    full = client.get(f"/api/v1/public/articles/{slug}").json()
    assert "J. Photographer" in full["featured_image"]["credit"] and full["featured_image"]["license"]
    feed = client.get("/api/v1/public/feed.xml")
    assert feed.status_code == 200 and "Bangladesh garment factories reopen" in feed.text

    admin = login(client, "admin@example.com")
    # Images used by an article cannot be deleted.
    assert admin.delete(f"/images/{image['id']}").status_code == 409

    # 8. Unpublish removes it from the public API.
    un = admin.post(f"/articles/{art_id}/unpublish", json={"destination_id": dest["id"]})
    assert un.status_code == 200 and un.json()["article"]["status"] == "unpublished"
    assert client.get(f"/api/v1/public/articles/{slug}").status_code == 404

    # 9. Audit trail recorded every step.
    actions = {a["action"] for a in admin.get("/activity/audit", params={"page_size": 200}).json()["items"]}
    for expected in ("source.created", "source.refreshed", "assignment.created", "image.approved", "article.updated",
                     "article.approved", "publishing.publish_succeeded", "publishing.unpublish_succeeded", "auth.setup"):
        assert any(a.startswith(expected) for a in actions), (expected, sorted(actions))
    history = admin.get(f"/articles/{art_id}/history").json()
    assert len(history) >= 5
    csv = admin.get("/activity/audit", params={"format": "csv"})
    assert csv.status_code == 200 and csv.text.startswith("time,actor,action")

    # 10. Analytics, search, notifications.
    ov = admin.get("/analytics/overview", params={"tz": "Asia/Dhaka"}).json()
    assert ov["metrics"]["active_assignments"] == 1 and ov["status"]["active_sources"] == 1
    for name in ("volume", "countries", "publishers", "categories", "assignments", "sources", "publications", "throughput"):
        r = admin.get(f"/analytics/reports/{name}")
        assert r.status_code == 200, (name, r.text)
    assert admin.get("/analytics/reports/countries", params={"format": "csv"}).status_code == 200
    s = admin.get("/search", params={"q": "garment"}).json()
    assert {g["type"]: len(g["items"]) for g in s["groups"]}["news"] == 2
    assert admin.get("/search/history").json()["items"] == ["garment"]
    notes = admin.get("/notifications").json()
    assert notes["unread"] >= 1  # high-priority assignment matches notify
    assert admin.post("/notifications/read-all").status_code == 200
    assert admin.get("/notifications").json()["unread"] == 0


def _approved_article(admin: Session_) -> dict:
    a = admin.post("/articles", json={"headline": "Webhook story", "body_html": LONG,
                                      "excerpt": "Short"}).json()
    admin.post(f"/articles/{a['id']}/transition", json={"target": "in_review"})
    return admin.post(f"/articles/{a['id']}/transition", json={"target": "approved"}).json()


def test_webhook_publishing_signature_and_retry(client, web):
    admin = setup_admin(client)
    hook = "https://site.example.net/newswire-hook"
    received: list[httpx.Request] = []
    state = {"fail": True}

    def handler(request: httpx.Request):
        received.append(request)
        if state["fail"]:
            return httpx.Response(503, text="down")
        return httpx.Response(200, json={"id": "remote-1", "url": "https://site.example.net/story"})

    web.routes[hook] = handler
    d = admin.post("/publishing/destinations", json={"name": "Site", "kind": "webhook", "base_url": hook, "secret": "s3cret-value"})
    assert d.status_code == 201 and d.json()["has_secret"] is True and "secret" not in d.json()
    art = _approved_article(admin)

    first = admin.post(f"/articles/{art['id']}/publish", json={"destination_id": d.json()["id"]}).json()
    assert first["job"]["status"] == "pending" and first["job"]["attempts"] == 1  # retryable failure, queued again
    state["fail"] = False
    job_id = first["job"]["id"]
    assert admin.post(f"/publishing/jobs/{job_id}/retry").status_code == 200
    run = admin.post("/publishing/jobs/run").json()
    assert run.get("succeeded", 0) == 1, run

    req = received[-1]
    sig = hmac.new(b"s3cret-value", req.content, hashlib.sha256).hexdigest()
    assert req.headers["x-newswire-signature"] == f"sha256={sig}"
    payload = json.loads(req.content)
    assert payload["event"] == "article.published" and payload["article"]["headline"] == "Webhook story"
    pubs = admin.get(f"/articles/{art['id']}").json()["publications"]
    assert pubs[0]["remote_url"] == "https://site.example.net/story"


def test_wordpress_publishing(client, web):
    admin = setup_admin(client)
    api = "https://blog.example.net/wp-json/wp/v2"
    calls: list[tuple[str, str]] = []

    def posts(request: httpx.Request):
        calls.append((request.method, str(request.url)))
        assert request.headers["authorization"].startswith("Basic ")
        body = json.loads(request.content)
        return httpx.Response(201, json={"id": 101, "link": "https://blog.example.net/?p=101",
                                         "status": "publish" if body["status"] == "publish" else "draft"})

    web.routes[f"{api}/posts"] = posts
    web.routes[f"{api}/users/me?context=edit"] = lambda r: httpx.Response(200, json={"name": "Editor Bot"})
    d = admin.post("/publishing/destinations", json={"name": "Blog", "kind": "wordpress", "base_url": "https://blog.example.net/",
                                                     "username": "bot", "secret": "abcd efgh ijkl mnop"}).json()
    test = admin.post(f"/publishing/destinations/{d['id']}/test").json()
    assert test == {"ok": True, "message": "Connected as Editor Bot"}
    art = _approved_article(admin)
    r = admin.post(f"/articles/{art['id']}/publish", json={"destination_id": d["id"], "mode": "draft"}).json()
    assert r["job"]["status"] == "succeeded", r
    pub = r["article"]["publications"][0]
    assert pub["remote_id"] == "101" and pub["remote_status"] == "draft"

    web.routes[f"{api}/posts"] = lambda req: httpx.Response(401, json={"message": "bad auth"})
    art2 = _approved_article(admin)
    r2 = admin.post(f"/articles/{art2['id']}/publish", json={"destination_id": d["id"]}).json()
    assert r2["job"]["status"] == "failed" and "credentials" in r2["job"]["last_error"]


def test_noncommercial_image_blocked_on_commercial_destination(client, web, db):
    admin = setup_admin(client)
    img_url = "https://images.example.org/nc.jpg"
    web.add(img_url, jpeg_bytes(), headers={"content-type": "image/jpeg"})
    img = admin.post("/images", json={"candidate": {"provider": "openverse", "provider_id": "nc-1", "original_url": img_url,
                                                     "license_code": "by-nc", "license_version": "4.0",
                                                     "attribution_text": "Photo by A, CC BY-NC 4.0"}}).json()
    assert admin.post(f"/images/{img['id']}/approve").status_code == 200
    a = admin.post("/articles", json={"headline": "NC image story", "body_html": LONG,
                                      "featured_image_id": img["id"]}).json()
    admin.post(f"/articles/{a['id']}/transition", json={"target": "in_review"})
    admin.post(f"/articles/{a['id']}/transition", json={"target": "approved"})
    dest = next(d for d in admin.get("/publishing/destinations").json() if d["kind"] == "builtin")
    assert dest["is_commercial"] is True
    r = admin.post(f"/articles/{a['id']}/publish", json={"destination_id": dest["id"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "checks_failed"


def test_settings_and_backups(client):
    admin = setup_admin(client)
    s = admin.get("/settings").json()
    assert s["values"]["publishing"]["auto_publish_policy"] is False
    bad = admin.put("/settings/clustering", json={"headline_threshold": 5})
    assert bad.status_code == 422
    ok = admin.put("/settings/clustering", json={"headline_threshold": 90})
    assert ok.status_code == 200 and ok.json()["value"]["headline_threshold"] == 90
    assert admin.put("/settings/nope", json={}).status_code == 404

    b = admin.post("/system/backups")
    assert b.status_code == 201, b.text
    backup = b.json()
    assert backup["status"] == "completed" and backup["verify_status"].startswith("verified")
    v = admin.post(f"/system/backups/{backup['id']}/verify").json()
    assert v["ok"] is True
    dl = admin.get(f"/system/backups/{backup['id']}/download")
    assert dl.status_code == 200 and len(dl.content) == backup["size_bytes"]
    status = admin.get("/system/status").json()
    assert status["database"]["dialect"] in ("sqlite", "postgresql")
    assert status["last_backup"]["filename"] == backup["filename"]
    assert admin.delete(f"/system/backups/{backup['id']}").status_code == 200
