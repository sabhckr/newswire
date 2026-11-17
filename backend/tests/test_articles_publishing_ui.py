"""API behaviour the article editor and publishing pages rely on."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tests.test_api import LONG, client, login, make_user, setup_admin, web  # noqa: F401  (fixtures)


def _approved(admin) -> dict:
    a = admin.post("/articles", json={"headline": "Scheduling story", "body_html": LONG, "excerpt": "x"}).json()
    admin.post(f"/articles/{a['id']}/transition", json={"target": "in_review"})
    return admin.post(f"/articles/{a['id']}/transition", json={"target": "approved"}).json()


def test_preview_is_a_styled_document(client):
    admin = setup_admin(client)
    a = admin.post("/articles", json={"headline": "Preview <story>", "body_html": LONG}).json()
    r = admin.get(f"/articles/{a['id']}/preview")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.text.startswith("<!doctype html>") and "<style>" in r.text
    assert "Preview &lt;story&gt;" in r.text
    assert "frame-ancestors 'self'" in r.headers["content-security-policy"]


def test_jobs_status_filter_and_schedule_cancellation(client):
    admin = setup_admin(client)
    art = _approved(admin)
    dest = next(d for d in admin.get("/publishing/destinations").json() if d["kind"] == "builtin")
    when = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    r = admin.post(f"/articles/{art['id']}/publish", json={"destination_id": dest["id"], "mode": "scheduled", "scheduled_for": when})
    assert r.status_code == 200, r.text
    assert r.json()["article"]["status"] == "scheduled" and r.json()["job"]["status"] == "pending"

    pending = admin.get("/publishing/jobs", params={"status": ["pending"]}).json()
    assert pending["total"] == 1
    # "Retry"/"run now" must not publish a scheduled job before its time.
    early = admin.post(f"/publishing/jobs/{pending['items'][0]['id']}/retry")
    assert early.status_code == 409, early.text
    assert admin.get("/publishing/jobs", params={"status": ["succeeded"]}).json()["total"] == 0

    # Moving the article out of "scheduled" cancels the queued publication.
    back = admin.post(f"/articles/{art['id']}/transition", json={"target": "approved"})
    assert back.status_code == 200 and back.json()["status"] == "approved" and back.json()["scheduled_at"] is None
    assert admin.get("/publishing/jobs", params={"status": ["pending"]}).json()["total"] == 0
    cancelled = admin.get("/publishing/jobs", params={"status": ["cancelled"]}).json()
    assert cancelled["total"] == 1 and "moved from scheduled" in cancelled["items"][0]["last_error"]


def test_edit_by_non_approver_cancels_schedule(client):
    admin = setup_admin(client)
    make_user(admin, "writer@example.com", "researcher")
    art = _approved(admin)
    dest = next(d for d in admin.get("/publishing/destinations").json() if d["kind"] == "builtin")
    when = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert admin.post(f"/articles/{art['id']}/publish",
                      json={"destination_id": dest["id"], "mode": "scheduled", "scheduled_for": when}).status_code == 200

    writer = login(client, "writer@example.com")
    current = writer.get(f"/articles/{art['id']}").json()
    r = writer.put(f"/articles/{art['id']}", json={"headline": "Rewritten headline", "version": current["version"]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "in_review" and r.json()["scheduled_at"] is None
    jobs = writer.get("/publishing/jobs", params={"article_id": art["id"]}).json()["items"]
    assert [j["status"] for j in jobs] == ["cancelled"]
    assert "approval again" in jobs[0]["last_error"]
