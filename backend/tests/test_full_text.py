"""Full story text: extraction from saved page shapes, and the news API that reads it on request."""

from __future__ import annotations

import json

import httpx
import pytest

from app.collectors.article_text import extract
from app.core.text import content_hash
from app.models import NewsItem, Source
from tests.test_api import Web, client, setup_admin  # noqa: F401  (fixtures)

URL = "https://news.example.com/bangladesh/12345"

PARAS = [
    "ঢাকায় আজ সকালে ভারী বৃষ্টি হয়েছে। আবহাওয়া অফিস জানিয়েছে, বৃষ্টি আরও দুদিন চলতে পারে।",
    "রাজধানীর বিভিন্ন সড়কে জলাবদ্ধতা দেখা দিয়েছে। মিরপুর, ধানমন্ডি ও মতিঝিলে যানবাহন চলাচল ব্যাহত হয়েছে।",
    "সিটি করপোরেশন বলেছে, পানি সরাতে কাজ চলছে। নাগরিকদের প্রয়োজন ছাড়া বাইরে না যাওয়ার পরামর্শ দেওয়া হয়েছে।",
]


def page(body: str, head: str = "") -> str:
    return (
        "<!doctype html><html lang='bn'><head><title>বৃষ্টি | Example</title>"
        "<meta property='og:title' content='ঢাকায় ভারী বৃষ্টি'>"
        "<meta property='og:image' content='https://news.example.com/lead.jpg'>"
        f"{head}</head><body><nav><a href='/'>Home</a><a href='/x'>Politics</a></nav>{body}"
        "<div class='related-news'><p><a href='/a'>অন্য একটি খবর যা এখানে থাকা উচিত নয় এবং বেশ লম্বা</a></p></div>"
        "<footer><p>© Example News. All rights reserved and more footer text here.</p></footer></body></html>"
    )


def test_json_ld_article_body():
    ld = {"@context": "https://schema.org", "@type": "NewsArticle", "headline": "ঢাকায় ভারী বৃষ্টি",
          "author": {"@type": "Person", "name": "নিজস্ব প্রতিবেদক"}, "datePublished": "2026-11-17T08:00:00+06:00",
          "articleBody": "\n".join(PARAS)}
    a = extract(page("<div>teaser</div>", f"<script type='application/ld+json'>{json.dumps(ld, ensure_ascii=False)}</script>"), URL)
    assert a.method == "json_ld" and a.paragraphs == PARAS
    assert a.author == "নিজস্ব প্রতিবেদক" and a.published_at is not None
    assert a.lead_image == "https://news.example.com/lead.jpg"


def test_escaped_html_in_json_ld_is_cleaned():
    body = "".join(f"&lt;p&gt;{p}&lt;/p&gt;" for p in PARAS)
    ld = {"@type": ["NewsArticle"], "articleBody": body}
    a = extract(page("", f"<script type='application/ld+json'>{json.dumps({'@graph': [ld]}, ensure_ascii=False)}</script>"), URL)
    assert a.paragraphs == PARAS


def test_itemprop_body_inside_a_widget_box_is_kept():
    body = "".join(f"<p>{p}</p>" for p in PARAS)
    html = page(f"<div class='widget'><div class='jw_article_body' itemprop='articleBody'>{body}"
                "<p>আরও পড়ুন: অন্য খবর</p></div></div>")
    a = extract(html, URL)
    assert a.method == "itemprop" and a.paragraphs == PARAS


def test_density_picks_the_story_not_the_page():
    body = "".join(f"<p>{p}</p>" for p in PARAS)
    html = page(f"<!-- news body --><div class='content'><h1>ঢাকায় ভারী বৃষ্টি</h1><div class='detail-text'>{body}"
                "<figure><img src='/photo.jpg' width='800'><figcaption>জলাবদ্ধ সড়ক</figcaption></figure>"
                "<iframe src='https://www.youtube.com/embed/abc123'></iframe></div></div>"
                "<div class='share-buttons'><p>Share this on Facebook, WhatsApp and every other social site</p></div>")
    a = extract(html, URL)
    assert a.method == "density"
    assert a.paragraphs == PARAS
    assert a.images == [{"url": "https://news.example.com/photo.jpg", "caption": "জলাবদ্ধ সড়ক"}]
    assert a.videos == ["https://www.youtube.com/embed/abc123"]


def test_one_long_block_is_split_into_paragraphs():
    text = " ".join(f"এটি {i} নম্বর বাক্য, যেখানে ঘটনার বিস্তারিত বিবরণ দেওয়া হয়েছে এবং আরও তথ্য আছে।" for i in range(20))
    a = extract(page("", f"<script type='application/ld+json'>{json.dumps({'@type': 'NewsArticle', 'articleBody': text}, ensure_ascii=False)}</script>"), URL)
    assert len(a.paragraphs) == 7 and "".join(a.paragraphs).replace(" ", "") == text.replace(" ", "")


# --- API ---------------------------------------------------------------------------------------


@pytest.fixture()
def site(monkeypatch):
    w = Web()

    def fake_client(**_kw):
        return httpx.Client(transport=httpx.MockTransport(w.handler), follow_redirects=False)

    import app.api.news

    monkeypatch.setattr(app.api.news, "make_client", fake_client)
    return w


def make_item(db, respect_robots=True) -> NewsItem:
    src = Source(name="Example", publisher="Example News", source_type="rss", feed_url="https://news.example.com/rss",
                 respect_robots=respect_robots, is_active=True)
    db.add(src)
    db.flush()
    item = NewsItem(source_id=src.id, title="ঢাকায় ভারী বৃষ্টি", url=URL, normalized_url=URL,
                    content_hash=content_hash("ঢাকায় ভারী বৃষ্টি"), summary="রাজধানীতে ভারী বৃষ্টি।")
    db.add(item)
    db.commit()
    return item


def story_page() -> str:
    return page("<article class='story'>" + "".join(f"<p>{p}</p>" for p in PARAS) + "</article>")


def test_full_text_is_read_once_and_kept(client, db, site):  # noqa: F811
    admin = setup_admin(client)
    item = make_item(db)
    site.add(URL, story_page(), headers={"content-type": "text/html; charset=utf-8"})
    r = admin.post(f"/news/{item.id}/full-text")
    assert r.status_code == 200, r.text
    assert r.json()["paragraphs"] == PARAS
    fetched = len(site.requests)
    assert admin.post(f"/news/{item.id}/full-text").json()["paragraphs"] == PARAS
    assert len(site.requests) == fetched  # served from the stored copy
    assert admin.get(f"/news/{item.id}").json()["extra"]["full_text"]["paragraphs"] == PARAS


def test_full_text_respects_robots(client, db, site):  # noqa: F811
    admin = setup_admin(client)
    item = make_item(db)
    site.add("https://news.example.com/robots.txt", "User-agent: *\nDisallow: /bangladesh/\n", headers={"content-type": "text/plain"})
    site.add(URL, story_page(), headers={"content-type": "text/html"})
    r = admin.post(f"/news/{item.id}/full-text")
    assert r.status_code == 422 and "robots.txt" in r.json()["error"]["message"]
    assert all(str(q.url) != URL for q in site.requests)


def test_paywall_is_reported_not_bypassed(client, db, site):  # noqa: F811
    admin = setup_admin(client)
    item = make_item(db)
    site.add(URL, "Subscribe", status=402, headers={"content-type": "text/html"})
    r = admin.post(f"/news/{item.id}/full-text")
    assert r.status_code == 422 and "payment required" in r.json()["error"]["message"]


def test_start_article_from_news(client, db, site):  # noqa: F811
    admin = setup_admin(client)
    item = make_item(db)
    site.add(URL, story_page(), headers={"content-type": "text/html"})
    r = admin.post(f"/news/{item.id}/article")
    assert r.status_code == 201, r.text
    art = r.json()
    assert art["headline"] == "ঢাকায় ভারী বৃষ্টি" and art["status"] == "draft"
    for p in PARAS:
        assert f"<p>{p}</p>" in art["body_html"]
    assert URL in art["body_html"] and "Example News" in art["body_html"]
    assert [s["url"] for s in art["sources"]] == [URL]


def test_start_article_falls_back_to_summary(client, db, site):  # noqa: F811
    admin = setup_admin(client)
    item = make_item(db)
    site.add(URL, "gone", status=404, headers={"content-type": "text/html"})
    r = admin.post(f"/news/{item.id}/article")
    assert r.status_code == 201, r.text
    assert "<p>রাজধানীতে ভারী বৃষ্টি।</p>" in r.json()["body_html"]
    assert "feed summary" in r.json()["notice"]
