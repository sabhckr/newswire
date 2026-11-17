"""Story clustering: related reports group, unrelated reports with one shared word do not."""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select

from app.clustering.engine import cluster_items, make_sig, merge_clusters, similarity, split_cluster
from app.core.db import utcnow
from app.models import NewsItem, Source, StoryCluster, StoryClusterItem
from app.services.settings import DEFAULTS

CFG = DEFAULTS["clustering"]


def sig(title, summary="", countries=()):
    return make_sig(uuid.uuid4(), title, summary, None, utcnow(), None, countries)


def test_near_identical_headlines_cluster():
    a = sig("Bangladesh cabinet approves Padma rail link funding")
    b = sig("Padma rail link funding approved by Bangladesh cabinet")
    assert similarity(a, b, CFG)[1] == "headline"


def test_single_shared_keyword_never_clusters():
    a = sig("Bangladesh wins cricket series against Zimbabwe", "Sports report about cricket.")
    b = sig("Bangladesh floods displace thousands in Sylhet", "Monsoon rains caused floods.")
    assert similarity(a, b, CFG) is None


def test_subset_headline_does_not_cluster():
    a = sig("Bangladesh")
    b = sig("Bangladesh floods displace thousands")
    assert similarity(a, b, CFG) is None


def test_keyword_overlap_with_shared_country_clusters():
    a = sig("Cabinet clears Dhaka metro rail extension",
            "The cabinet cleared the metro rail extension to Narayanganj, costing billions, officials said.", ["BD"])
    b = sig("Metro extension to Narayanganj gets go-ahead",
            "Officials said the cabinet cleared the metro rail extension to Narayanganj costing billions.", ["BD"])
    res = similarity(a, b, CFG)
    assert res is not None and res[1] == "keywords"


def _item(db, title, summary="", source=None, hours=1):
    now = utcnow()
    it = NewsItem(title=title, summary=summary, url=f"https://x.example.com/{uuid.uuid4()}",
                  normalized_url=f"https://x.example.com/{uuid.uuid4()}", content_hash=uuid.uuid4().hex,
                  source_id=source.id if source else None, retrieved_at=now, sort_at=now - timedelta(hours=hours))
    db.add(it)
    db.flush()
    return it


def test_cluster_items_creates_and_joins_and_manual_ops(db):
    s1 = Source(name="A", publisher="Pub A", feed_url="https://a.example.com/rss")
    s2 = Source(name="B", publisher="Pub B", feed_url="https://b.example.com/rss")
    s3 = Source(name="C", publisher="Pub C", feed_url="https://c.example.com/rss")
    db.add_all([s1, s2, s3])
    db.flush()
    a = _item(db, "Bangladesh cabinet approves Padma rail link funding", source=s1, hours=3)
    cluster_items(db, [a])
    assert db.scalar(select(StoryCluster)) is None, "a single report is not a cluster"
    b = _item(db, "Padma rail link funding approved by Bangladesh cabinet", source=s2, hours=2)
    c = _item(db, "Bangladesh cabinet approves Padma rail link funding package", source=s3, hours=1)
    u = _item(db, "Bangladesh beats Zimbabwe in cricket", source=s3)
    stats = cluster_items(db, [b, c, u])
    assert stats == {"joined": 1, "created": 1}
    cluster = db.scalar(select(StoryCluster))
    assert cluster.item_count == 3 and cluster.publisher_count == 3
    assert cluster.title == a.title and cluster.title_source == "source_headline"
    assert cluster.representative_item_id == a.id
    assert db.scalar(select(StoryClusterItem).where(StoryClusterItem.news_item_id == u.id)) is None

    new = split_cluster(db, cluster, [c.id], title="Funding package details")
    db.refresh(cluster)
    assert cluster.item_count == 2 and new.item_count == 1 and new.title == "Funding package details"
    merged = merge_clusters(db, cluster, [new])
    assert merged.item_count == 3
    assert db.scalar(select(StoryCluster).where(StoryCluster.id == new.id)) is None
