"""Unit tests for safety-critical rules: SSRF guard, licences, geography, article workflow, backups, scheduler."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.articles.content import sanitize_html
from app.articles.state import TransitionError, available_transitions, check_transition
from app.core.http import UnsafeURLError, is_public_address, make_client, safe_request, validate_url
from app.geography.classify import classify, detect_dateline
from app.geography.gazetteer import get_gazetteer
from app.images.licenses import evaluate_use, has_blocking, normalize_license
from app.models import NewsItem, ScheduledJob, Source
from app.publishing.connectors import verify_signature
from app.security.permissions import ROLE_PERMISSIONS

# ---- SSRF guard ---------------------------------------------------------------------------


@pytest.mark.parametrize("addr", ["127.0.0.1", "10.1.2.3", "172.16.0.9", "192.168.1.1", "169.254.169.254", "100.64.0.1",
                                  "0.0.0.0", "::1", "fc00::1", "fe80::1", "::ffff:127.0.0.1", "224.0.0.1"])
def test_private_and_special_addresses_are_not_public(addr):
    assert not is_public_address(addr)


@pytest.mark.parametrize("addr", ["93.184.216.34", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses(addr):
    assert is_public_address(addr)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "http://localhost/feed", "http://127.0.0.1/",
                                 "http://[::1]/", "http://user:pw@example.com/", "http://169.254.169.254/latest/meta-data",
                                 "http://printer.local/", "http://example.com:99999/"])
def test_validate_url_rejects_unsafe(url):
    with pytest.raises(UnsafeURLError):
        validate_url(url, allow_private=False)


def test_redirect_to_private_address_is_blocked():
    def handler(request: httpx.Request):
        return httpx.Response(302, headers={"location": "http://10.0.0.5/admin"})

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    with pytest.raises(UnsafeURLError):
        safe_request(client, "GET", "https://feeds.example.com/rss", allow_private=False)


def test_guarded_client_refuses_loopback_at_connect_time():
    # Even if static validation is skipped, the connect-time guard refuses private addresses.
    with make_client(timeout=2, allow_private=False) as client, pytest.raises(httpx.ConnectError):
        client.get("http://127.0.0.1:9/")


def test_oversized_response_is_cut_off():
    from app.core.http import ResponseTooLarge

    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 5000)))
    with pytest.raises(ResponseTooLarge):
        safe_request(client, "GET", "https://example.com/big", max_bytes=1000, allow_private=False)


# ---- Licences -----------------------------------------------------------------------------


@pytest.mark.parametrize("raw,url,expected", [
    ("CC BY-SA 4.0", None, ("by-sa", "4.0")),
    ("by-nc-nd", None, ("by-nc-nd", "")),
    (None, "https://creativecommons.org/licenses/by/2.0/", ("by", "2.0")),
    ("Public domain", None, ("pdm", "")),
    ("CC0", None, ("cc0", "1.0")),
    ("All rights reserved", None, ("unknown", "")),
])
def test_normalize_license(raw, url, expected):
    code, version = normalize_license(raw, url=url)
    assert code == expected[0]
    if expected[1]:
        assert version == expected[1]


def test_license_rules_block_unsafe_uses():
    unknown = evaluate_use(license_code="unknown", allows_commercial=None, allows_derivatives=None,
                           requires_attribution=None, share_alike=False, attribution_text=None)
    assert has_blocking(unknown)
    nc = evaluate_use(license_code="by-nc", allows_commercial=False, allows_derivatives=True, requires_attribution=True,
                      share_alike=False, attribution_text="Photo: A", commercial_publication=True)
    assert {w.code for w in nc if w.level == "error"} == {"non_commercial"}
    nc_noncommercial_site = evaluate_use(license_code="by-nc", allows_commercial=False, allows_derivatives=True,
                                         requires_attribution=True, share_alike=False, attribution_text="Photo: A",
                                         commercial_publication=False)
    assert not has_blocking(nc_noncommercial_site)
    nd = evaluate_use(license_code="by-nd", allows_commercial=True, allows_derivatives=False, requires_attribution=True,
                      share_alike=False, attribution_text="Photo: A", modifications_planned=True)
    assert "no_derivatives" in {w.code for w in nd if w.level == "error"}
    no_attr = evaluate_use(license_code="by", allows_commercial=True, allows_derivatives=True, requires_attribution=True,
                           share_alike=False, attribution_text="  ")
    assert "attribution_missing" in {w.code for w in no_attr}
    pending = evaluate_use(license_code="cc0", allows_commercial=True, allows_derivatives=True, requires_attribution=False,
                           share_alike=False, attribution_text=None, approval_status="pending")
    assert "not_approved" in {w.code for w in pending}
    ok = evaluate_use(license_code="cc0", allows_commercial=True, allows_derivatives=True, requires_attribution=False,
                      share_alike=False, attribution_text=None, approval_status="approved")
    assert not has_blocking(ok)


# ---- Geography ----------------------------------------------------------------------------


def test_dateline_detection():
    assert detect_dateline("DHAKA, Nov 17 (Reuters) - Factories reopened") == "DHAKA"
    assert detect_dateline("NAIROBI (AP) — Kenya's president said") == "NAIROBI"
    assert detect_dateline("The minister said on Monday that...") is None


def test_classification_roles_and_ambiguity(db):
    gaz = get_gazetteer(db)
    src = Source(name="S", publisher="P", feed_url="https://x.example/rss", country_code="GB")
    item = NewsItem(title="Floods hit Bangladesh as Dhaka braces for more rain",
                    summary="DHAKA, Nov 17 (Reuters) - Heavy rain flooded parts of the capital.", url="https://x.example/1")
    roles = {(a.role, gaz.entities[a.entity_id].name if a.entity_id else None): a.confidence
             for a in classify(item, src, gaz)}
    assert roles[("publisher", "United Kingdom")] == 1.0
    assert roles[("event", "Dhaka")] >= 0.7
    assert roles[("mentioned", "Bangladesh")] >= 0.8
    # Common words are not places; ambiguous demonyms stay low-confidence.
    turkey = classify(NewsItem(title="How to roast a turkey", summary="", url="https://x.example/2"), None, gaz)
    assert not [a for a in turkey if a.entity_id and gaz.entities[a.entity_id].name == "Turkey" and a.confidence >= 0.5]


# ---- Articles -----------------------------------------------------------------------------


def test_state_machine_permissions():
    researcher = ROLE_PERMISSIONS["researcher"]
    editor = ROLE_PERMISSIONS["editor"]
    check_transition("draft", "in_review", researcher)
    with pytest.raises(TransitionError):
        check_transition("in_review", "approved", researcher)
    check_transition("in_review", "approved", editor)
    with pytest.raises(TransitionError):
        check_transition("draft", "published", editor)  # never straight from draft
    assert "published" not in available_transitions("in_review", editor)
    assert available_transitions("archived", editor) == ["draft"]


def test_sanitize_html_strips_active_content():
    dirty = ('<p onclick="x()">Hi <a href="javascript:alert(1)">x</a> <a href="https://ok.example">ok</a></p>'
             '<script>alert(1)</script><iframe src="https://evil.example"></iframe><img src=x onerror=alert(1)>')
    clean = sanitize_html(dirty)
    for bad in ("onclick", "javascript:", "<script", "<iframe", "onerror"):
        assert bad not in clean
    assert 'href="https://ok.example"' in clean and "noopener" in clean


def test_webhook_signature_verification():
    import hashlib
    import hmac

    body = b'{"event":"article.published"}'
    good = "sha256=" + hmac.new(b"k", body, hashlib.sha256).hexdigest()
    assert verify_signature("k", body, good)
    assert not verify_signature("k", body + b" ", good)
    assert not verify_signature("k", body, None)


# ---- Backups and scheduler ----------------------------------------------------------------


def test_backup_round_trip_restores_data(db, tmp_path):
    from app.core.db import get_engine
    from app.services import backups

    if get_engine().dialect.name != "sqlite":
        pytest.skip("SQLite round trip; PostgreSQL restore is exercised manually (see docs/RECOVERY.md)")
    db.add(Source(name="Keep me", publisher="P", feed_url="https://keep.example/rss"))
    db.commit()
    b = backups.create_backup(db, kind="manual", include_images=False)
    assert b.status == "completed", b.error
    from app.core.config import get_settings

    path = get_settings().backups_dir / b.filename
    target = tmp_path / "restored.db"
    result = backups.restore(path, database_url=f"sqlite:///{target}", restore_images=False)
    assert result["database"] == "sqlite"
    import sqlite3

    rows = sqlite3.connect(target).execute("select name from sources").fetchall()
    assert ("Keep me",) in rows

    # A tampered archive fails verification and is never restored.
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0xFF
    bad = tmp_path / "tampered.tar.gz"
    bad.write_bytes(bytes(data))
    ok, _ = backups.verify_file(bad)
    assert not ok
    with pytest.raises(backups.BackupError):
        backups.restore(bad, database_url=f"sqlite:///{tmp_path / 'x.db'}")


def test_scheduler_records_runs_and_failures(db):
    from app.scheduler import runner

    runner._record("unit_ok", lambda s: {"ok": 1}, 60)
    runner._record("unit_fail", lambda s: 1 / 0, 60)
    runner.heartbeat()
    db.expire_all()
    ok = db.get(ScheduledJob, "unit_ok")
    fail = db.get(ScheduledJob, "unit_fail")
    assert ok.last_status == "ok" and ok.run_count == 1 and ok.next_run_at is not None
    assert fail.last_status == "error" and "ZeroDivisionError" in fail.last_error and fail.failure_count == 1
    worker = db.scalar(select(ScheduledJob).where(ScheduledJob.name == "worker"))
    assert worker.heartbeat_at is not None and worker.worker_id == runner.WORKER_ID
    names = [n for n, _, _ in runner.job_specs()]
    assert names == ["collection", "publishing", "backups", "housekeeping", "publication_sync"]
