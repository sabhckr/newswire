"""One-step publishing: someone who may approve articles publishes a draft directly. The server
approves and publishes in the same request, or does neither."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.errors import AppError
from app.models import Article, PublishingDestination
from app.publishing.service import check_publication
from app.security import permissions as P
from tests.test_api import LONG, client, login, make_user, setup_admin, web  # noqa: F401  (fixtures)


def draft(admin, **extra) -> dict:
    r = admin.post("/articles", json={"headline": "Garment factories reopen", "body_html": LONG,
                                      "excerpt": "Workers returned on Monday.", **extra})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "draft"
    return r.json()


def builtin(admin) -> dict:
    return next(d for d in admin.get("/publishing/destinations").json() if d["kind"] == "builtin")


def test_an_administrator_publishes_a_draft_in_one_step(client):
    admin = setup_admin(client)
    art, dest = draft(admin), builtin(admin)
    r = admin.post(f"/articles/{art['id']}/publish-many", json={"targets": [{"destination_id": dest["id"]}]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["jobs"][0]["status"] == "succeeded", out["jobs"][0]
    assert out["article"]["status"] == "published" and out["article"]["approved_at"]
    listed = client.get("/api/v1/public/articles").json()
    assert [a["headline"] for a in listed["items"]] == ["Garment factories reopen"]
    # The approval is on record, marked as having happened by publishing.
    history = admin.get(f"/articles/{art['id']}/history").json()
    approved = [h for h in history if h["action"] == "article.approved"]
    assert len(approved) == 1 and approved[0]["details"]["by_publishing"] is True
    assert approved[0]["details"]["from"] == "draft"

    # Asking twice does not publish twice.
    again = admin.post(f"/articles/{art['id']}/publish-many", json={"targets": [{"destination_id": dest["id"]}]})
    assert again.status_code == 200 and again.json()["duplicate_request"] is True


def test_the_single_destination_endpoint_and_review_stage_work_too(client):
    admin = setup_admin(client)
    art, dest = draft(admin), builtin(admin)
    assert admin.post(f"/articles/{art['id']}/transition", json={"target": "in_review"}).status_code == 200
    r = admin.post(f"/articles/{art['id']}/publish", json={"destination_id": dest["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["job"]["status"] == "succeeded" and r.json()["article"]["status"] == "published"


def test_a_draft_that_fails_the_checks_stays_a_draft(client):
    admin = setup_admin(client)
    art, dest = draft(admin, body_html="<p>Too short.</p>"), builtin(admin)
    r = admin.post(f"/articles/{art['id']}/publish-many", json={"targets": [{"destination_id": dest["id"]}]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "checks_failed", r.text
    after = admin.get(f"/articles/{art['id']}").json()
    assert after["status"] == "draft" and after["approved_at"] is None
    assert not [h for h in admin.get(f"/articles/{art['id']}/history").json() if h["action"] == "article.approved"]
    assert client.get("/api/v1/public/articles").json()["items"] == []
    assert admin.get("/publishing/jobs", params={"article_id": art["id"]}).json()["items"] == []


def test_one_bad_destination_leaves_the_draft_alone(client):
    admin = setup_admin(client)
    art, dest = draft(admin), builtin(admin)
    off = admin.post("/publishing/destinations", json={"name": "Hook", "kind": "webhook", "base_url": "https://example.org/hook",
                                                       "secret": "0123456789abcdef0123456789abcdef"})
    assert off.status_code == 201, off.text
    assert admin.put(f"/publishing/destinations/{off.json()['id']}", json={"is_active": False}).status_code == 200
    r = admin.post(f"/articles/{art['id']}/publish-many",
                   json={"targets": [{"destination_id": dest["id"]}, {"destination_id": off.json()["id"]}]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "destination_inactive", r.text
    after = admin.get(f"/articles/{art['id']}").json()
    assert after["status"] == "draft" and after["approved_at"] is None
    assert client.get("/api/v1/public/articles").json()["items"] == []


def test_scheduling_a_draft_approves_and_schedules_it(client):
    admin = setup_admin(client)
    art, dest = draft(admin), builtin(admin)
    when = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    r = admin.post(f"/articles/{art['id']}/publish-many",
                   json={"mode": "scheduled", "scheduled_for": when, "targets": [{"destination_id": dest["id"]}]})
    assert r.status_code == 200, r.text
    assert r.json()["article"]["status"] == "scheduled" and r.json()["article"]["approved_at"]
    assert client.get("/api/v1/public/articles").json()["items"] == []


def test_people_who_may_not_approve_still_need_an_approval(client):
    admin = setup_admin(client)
    art, dest = draft(admin), builtin(admin)
    make_user(admin, "researcher@example.com", "researcher")
    researcher = login(client, "researcher@example.com")
    r = researcher.post(f"/articles/{art['id']}/publish-many", json={"targets": [{"destination_id": dest["id"]}]})
    assert r.status_code == 403, r.text

    # The rule itself, for a role that could publish but not approve.
    class PublisherOnly:
        id = None
        permissions = {P.READ, P.ARTICLES_PUBLISH}

        def can(self, perm: str) -> bool:
            return perm in self.permissions

    from app.core.db import SessionLocal

    db = SessionLocal()
    try:
        article = db.get(Article, __import__("uuid").UUID(art["id"]))
        destination = db.get(PublishingDestination, __import__("uuid").UUID(dest["id"]))
        with pytest.raises(AppError) as refused:
            check_publication(db, article, destination, "publish", actor=PublisherOnly())
        assert refused.value.code == "not_approved"
    finally:
        db.close()
    assert admin.get(f"/articles/{art['id']}").json()["status"] == "draft"
