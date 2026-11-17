"""News ingestion: fetch (network only, thread-safe) then process (DB, single thread)."""

from __future__ import annotations

import logging
import random
import threading
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.collectors import robots
from app.collectors.dedup import HeadlineIndex, find_content_duplicate, find_same_article, window_start
from app.collectors.page_meta import clean_page_title, fetch_page_metadata, looks_like_article
from app.collectors.parsers import FeedParseError, ParsedEntry, looks_like_sitemap, parse_feed, parse_sitemap
from app.core.config import get_settings
from app.core.db import is_postgres, utcnow
from app.core.http import ResponseTooLarge, UnsafeURLError, describe_error, make_client, safe_request
from app.core.text import bangla_share, content_hash
from app.core.urls import host_of, safe_normalize
from app.models import (
    AssignmentKeyword,  # noqa: F401  (ensures mappers are configured)
    EditorialAssignment,
    NewsItem,
    NewsItemSource,
    Source,
    SourceFetchLog,
)
from app.models.assignments import assignment_sources
from app.services.settings import get_setting

log = logging.getLogger(__name__)

ClientFactory = Callable[[], httpx.Client]
MAX_PAGE_LOOKUPS_PER_FETCH = 10
# Entries without a headline (plain sitemaps) only get a page lookup while they are recent, so a
# new source does not walk the publisher's archive.
PAGE_LOOKUP_MAX_AGE = timedelta(days=2)
# Pages that gave no usable headline are not asked again for a while.
LOOKUP_MISS_TTL_SECONDS = 6 * 3600
_lookup_misses: dict[str, float] = {}
_misses_lock = threading.Lock()

# Countries whose clocks run a fixed offset ahead of UTC (no daylight saving). Feeds there often
# label local time as UTC, which puts a story published minutes ago hours into the future.
LOCAL_TIME_ZONES = {
    "BD": "Asia/Dhaka", "IN": "Asia/Kolkata", "NP": "Asia/Kathmandu", "BT": "Asia/Thimphu",
    "LK": "Asia/Colombo", "MM": "Asia/Yangon", "PK": "Asia/Karachi",
}


def correct_local_time_as_utc(published: datetime, now: datetime, country_code: str | None) -> datetime | None:
    """The real publication time when `published` is local time mislabelled as UTC, else None.

    Only times between 10 minutes and the zone's offset (plus 10 minutes) ahead of now qualify,
    so the corrected time is never in the future."""
    zone = LOCAL_TIME_ZONES.get((country_code or "").upper())
    if zone is None:
        return None
    offset = published.astimezone(ZoneInfo(zone)).utcoffset() or timedelta(0)
    ahead = published - now
    if timedelta(minutes=10) < ahead <= offset + timedelta(minutes=10):
        return published - offset
    return None


def _recent_miss(url: str) -> bool:
    with _misses_lock:
        until = _lookup_misses.get(url)
        return until is not None and until > time.monotonic()


def _remember_miss(url: str) -> None:
    now = time.monotonic()
    with _misses_lock:
        if len(_lookup_misses) > 5000:
            for k in [k for k, v in _lookup_misses.items() if v <= now] or list(_lookup_misses)[:1000]:
                _lookup_misses.pop(k, None)
        _lookup_misses[url] = now + LOOKUP_MISS_TTL_SECONDS


@dataclass
class SourceSnapshot:
    id: uuid.UUID
    feed_url: str
    etag: str | None
    last_modified: str | None
    respect_robots: bool
    source_type: str = "rss"

    @classmethod
    def of(cls, s: Source) -> SourceSnapshot:
        return cls(s.id, s.feed_url, s.etag, s.last_modified, s.respect_robots, s.source_type or "rss")


@dataclass
class FetchOutcome:
    source_id: uuid.UUID
    status: str  # success | not_modified | error | blocked
    started_at: datetime
    duration_ms: int = 0
    http_status: int | None = None
    content: bytes | None = None
    content_type: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    error: str | None = None
    retry_after: int | None = None
    final_url: str | None = None
    note: str | None = None  # e.g. which child sitemap of a sitemap index was read


@dataclass
class ProcessResult:
    log: SourceFetchLog
    new_items: list[NewsItem] = field(default_factory=list)


def fetch_http(client: httpx.Client, snap: SourceSnapshot) -> FetchOutcome:
    """Network part only. Never touches the database, so it can run in a worker thread."""
    started = utcnow()
    t0 = time.monotonic()
    out = FetchOutcome(source_id=snap.id, status="error", started_at=started)
    try:
        if snap.respect_robots:
            allowed, reason = robots.is_allowed(client, snap.feed_url)
            if not allowed:
                out.status, out.error = "blocked", reason
                return out
        headers = {"Accept": "application/rss+xml, application/atom+xml, application/feed+json, application/json, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5"}
        if snap.etag:
            headers["If-None-Match"] = snap.etag
        if snap.last_modified:
            headers["If-Modified-Since"] = snap.last_modified
        resp = safe_request(client, "GET", snap.feed_url, headers=headers)
        out.http_status = resp.status_code
        out.final_url = resp.url
        if resp.status_code == 304:
            out.status = "not_modified"
        elif 200 <= resp.status_code < 300:
            out.status = "success"
            out.content = resp.content
            out.content_type = resp.headers.get("content-type")
            out.etag = resp.headers.get("etag")
            out.last_modified = resp.headers.get("last-modified")
        else:
            _http_error(out, resp)
        if out.status == "success" and looks_like_sitemap(out.content or b""):
            _follow_sitemap_index(client, snap, out)
    except (UnsafeURLError, ResponseTooLarge, httpx.HTTPError) as exc:
        out.error = describe_error(exc)
    except Exception as exc:  # pragma: no cover - defensive: one source must not stop the cycle
        log.exception("Unexpected fetch failure for %s", snap.feed_url)
        out.error = describe_error(exc)
    finally:
        out.duration_ms = int((time.monotonic() - t0) * 1000)
    return out


def _http_error(out: FetchOutcome, resp: httpx.Response) -> None:
    out.status = "error"
    out.error = f"HTTP {resp.status_code}"
    if resp.status_code in (401, 402, 403):
        out.error += " (access restricted; NEWSWIRE does not bypass access controls)"
    ra = resp.headers.get("retry-after")
    if ra and ra.isdigit():
        out.retry_after = int(ra)


def pick_child_sitemap(children: list[tuple[str, datetime | None]]) -> str | None:
    """The child of a sitemap index most likely to hold today's news: news sitemaps first,
    then the most recently modified, then the first listed."""
    if not children:
        return None
    news = [c for c in children if "news" in c[0].lower()]
    pool = news or children
    dated = [c for c in pool if c[1] is not None]
    if dated:
        return max(dated, key=lambda c: c[1])[0]
    return pool[0][0]


def _follow_sitemap_index(client: httpx.Client, snap: SourceSnapshot, out: FetchOutcome) -> None:
    """Replace a sitemap index with one child sitemap (one level only)."""
    try:
        index = parse_sitemap(out.content or b"")
    except FeedParseError:
        return  # leave it to the normal parse step to report
    if index.feed_format != "sitemap_index":
        return
    child = pick_child_sitemap(index.children)
    if child is None:
        out.status, out.error = "error", "Sitemap index lists no sitemaps"
        return
    if snap.respect_robots:
        allowed, reason = robots.is_allowed(client, child)
        if not allowed:
            out.status, out.error = "blocked", reason
            return
    resp = safe_request(client, "GET", child, headers={"Accept": "application/xml, text/xml;q=0.9, */*;q=0.5"})
    out.http_status = resp.status_code
    if not 200 <= resp.status_code < 300:
        _http_error(out, resp)
        out.error = f"{out.error} (child sitemap {child})"
        return
    out.content = resp.content
    out.content_type = resp.headers.get("content-type")
    # The index's validators say nothing about the child, so do not send them next time.
    out.etag = out.last_modified = None
    out.note = f"Sitemap index: read {child}"


def fetch_many(snaps: list[SourceSnapshot], client_factory: ClientFactory | None = None) -> list[FetchOutcome]:
    """Bounded-concurrency fetch. Sources on the same host are fetched sequentially with a delay."""
    settings = get_settings()
    factory = client_factory or make_client
    by_host: dict[str, list[SourceSnapshot]] = defaultdict(list)
    for s in snaps:
        by_host[host_of(s.feed_url)].append(s)

    def run_group(group: list[SourceSnapshot]) -> list[FetchOutcome]:
        results = []
        with factory() as client:
            for i, snap in enumerate(group):
                if i:
                    time.sleep(settings.per_host_delay_seconds)
                results.append(fetch_http(client, snap))
        return results

    outcomes: list[FetchOutcome] = []
    with ThreadPoolExecutor(max_workers=settings.fetch_concurrency, thread_name_prefix="fetch") as pool:
        for res in pool.map(run_group, by_host.values()):
            outcomes.extend(res)
    return outcomes


def effective_intervals(db: Session) -> tuple[dict[uuid.UUID, int], int | None]:
    """Assignment monitoring intervals that tighten source polling.

    Returns (per-source minimum, global minimum from assignments without selected sources).
    """
    per_source: dict[uuid.UUID, int] = {}
    global_min: int | None = None
    active = db.scalars(
        select(EditorialAssignment).where(
            EditorialAssignment.status == "active",
            EditorialAssignment.monitoring_interval_seconds.is_not(None),
        )
    ).all()
    for a in active:
        interval = int(a.monitoring_interval_seconds or 0)
        if not interval:
            continue
        if a.sources:
            for s in a.sources:
                per_source[s.id] = min(per_source.get(s.id, interval), interval)
        else:
            global_min = interval if global_min is None else min(global_min, interval)
    return per_source, global_min


def interval_for(source: Source, per_source: dict, global_min: int | None) -> int:
    settings = get_settings()
    candidates = [source.polling_interval_seconds or settings.default_poll_interval_seconds]
    if source.id in per_source:
        candidates.append(per_source[source.id])
    if global_min:
        candidates.append(global_min)
    return max(settings.min_poll_interval_seconds, min(candidates))


def _schedule_next(source: Source, outcome: FetchOutcome, interval: int) -> None:
    settings = get_settings()
    now = utcnow()
    if outcome.status in ("success", "not_modified"):
        source.next_fetch_at = now + timedelta(seconds=interval)
        return
    failures = max(1, source.consecutive_failures)
    delay = min(interval * (2 ** min(failures - 1, 12)), settings.max_backoff_seconds)
    if outcome.status == "blocked":
        delay = max(delay, 3600)
    if outcome.retry_after:
        delay = max(delay, min(outcome.retry_after, settings.max_backoff_seconds))
    delay = int(delay * random.uniform(0.9, 1.1))
    source.next_fetch_at = now + timedelta(seconds=max(delay, settings.min_poll_interval_seconds))


def _sort_time(published: datetime | None, retrieved: datetime) -> datetime:
    if published is None or published > retrieved + timedelta(minutes=10):
        return retrieved
    return published


def ingest_entries(
    db: Session,
    source: Source,
    entries: list[ParsedEntry],
    *,
    headline_index: HeadlineIndex,
    client: httpx.Client | None = None,
) -> tuple[list[NewsItem], int, int]:
    """Persist parsed entries. Returns (new_items, duplicates, skipped)."""
    now = utcnow()
    dedup_cfg = get_setting(db, "dedup")
    hash_since = now - timedelta(days=dedup_cfg["content_hash_window_days"])
    new_items: list[NewsItem] = []
    duplicates = skipped = 0
    page_lookups = 0
    seen_in_batch: set[str] = set()

    for entry in entries:
        normalized = safe_normalize(entry.url)
        if not normalized or (not entry.title and not source.extract_page_metadata):
            skipped += 1
            continue
        if normalized in seen_in_batch:
            duplicates += 1
            continue
        seen_in_batch.add(normalized)
        canonical = safe_normalize(entry.canonical_url)

        existing = find_same_article(db, normalized, canonical)
        if existing is not None:
            _link_source(db, existing.id, source.id, entry.guid)
            duplicates += 1
            continue

        title = entry.title
        published = entry.published_at
        summary = entry.summary
        image_urls = list(entry.image_urls)
        language = entry.language or source.language
        untitled = not title
        if untitled and (
            (published is not None and published < now - PAGE_LOOKUP_MAX_AGE) or _recent_miss(normalized)
        ):
            skipped += 1
            continue
        meta = None
        looked_up = False
        if source.extract_page_metadata and client is not None and page_lookups < MAX_PAGE_LOOKUPS_PER_FETCH:
            if page_lookups:
                time.sleep(get_settings().per_host_delay_seconds)
            page_lookups += 1
            looked_up = True
            meta = fetch_page_metadata(client, entry.url, source.respect_robots)
        # Entries without a title (plain sitemaps) take the page's own title when the page is a
        # story (not a section or tag page), or are skipped.
        if untitled:
            if meta is not None and meta.title and looks_like_article(entry.url, meta):
                title = clean_page_title(meta.title, entry.url, meta.site_name)
            elif looked_up:
                _remember_miss(normalized)
        if not title:
            skipped += 1
            continue
        if meta:
            canonical = canonical or safe_normalize(meta.canonical_url)
            if canonical and canonical != normalized:
                other = find_same_article(db, canonical, None)
                if other is not None:
                    _link_source(db, other.id, source.id, entry.guid)
                    duplicates += 1
                    continue
            summary = summary or meta.description
            if meta.image_url and meta.image_url not in image_urls:
                image_urls.append(meta.image_url)
            published = published or meta.published_at  # type: ignore[assignment]
            language = language or meta.language

        # Feeds sometimes declare the wrong language (WordPress sites in Bangla that say en-US,
        # English editions that say bn). The script of the text settles it.
        share = bangla_share(f"{title} {summary or ''}"[:600])
        if share >= 0.5:
            language = "bn"
        elif share < 0.1 and language and language.lower().startswith("bn"):
            fallback = source.language if source.language and not source.language.lower().startswith("bn") else None
            language = fallback
        # A publication time more than 10 minutes in the future is wrong. When it is the source
        # country's local time labelled as UTC it is corrected; otherwise it is kept for reference
        # but not used. Either way the time as given stays in `extra`.
        extra_dates = {}
        if published is not None and published > now + timedelta(minutes=10):
            fixed = correct_local_time_as_utc(published, now, source.country_code)
            if fixed is not None:
                extra_dates["published_at_as_given"] = published.isoformat()
                published = fixed
            else:
                extra_dates["rejected_published_at"] = published.isoformat()
                published = None

        chash = content_hash(title, summary)
        item = NewsItem(
            source_id=source.id,
            title=title,
            url=entry.url,
            normalized_url=normalized,
            canonical_url=canonical,
            guid=(entry.guid or None) and entry.guid[:1024],
            content_hash=chash,
            summary=summary,
            content=entry.content,
            author=entry.author,
            language=language,
            category_text=", ".join(entry.categories)[:500] or None,
            location_text=entry.location_text,
            image_urls=image_urls or None,
            published_at=published,
            retrieved_at=now,
            sort_at=_sort_time(published, now),
            extra={
                **extra_dates,
                "categories": entry.categories,
                **({"geo_point": list(entry.geo_point)} if entry.geo_point else {}),
            },
        )

        original = find_content_duplicate(db, chash, hash_since)
        if original is not None:
            item.is_duplicate = True
            item.duplicate_of_id = original.duplicate_of_id or original.id
            item.duplicate_reason = "content_hash"
            item.duplicate_score = 100.0
        else:
            hit = headline_index.best(title, dedup_cfg["headline_threshold"], dedup_cfg["min_headline_tokens"])
            if hit is not None:
                item.is_duplicate = True
                item.duplicate_of_id = hit[0]
                item.duplicate_reason = "headline_similarity"
                item.duplicate_score = hit[1]

        try:
            with db.begin_nested():
                db.add(item)
                db.flush()
        except IntegrityError:
            # Another process stored the same URL between our check and insert.
            existing = db.scalar(select(NewsItem).where(NewsItem.normalized_url == normalized))
            if existing is not None:
                _link_source(db, existing.id, source.id, entry.guid)
            duplicates += 1
            continue
        _link_source(db, item.id, source.id, entry.guid)
        headline_index.add(item.id, item.title, item.duplicate_of_id)
        new_items.append(item)
        if item.is_duplicate:
            duplicates += 1
    return new_items, duplicates, skipped


def _link_source(db: Session, item_id: uuid.UUID, source_id: uuid.UUID, guid: str | None) -> None:
    if db.get(NewsItemSource, (item_id, source_id)) is None:
        try:
            with db.begin_nested():
                db.add(NewsItemSource(news_item_id=item_id, source_id=source_id, guid=(guid or None) and guid[:1024]))
                db.flush()
        except IntegrityError:
            pass


def process_outcome(
    db: Session,
    source: Source,
    outcome: FetchOutcome,
    *,
    trigger: str,
    interval: int,
    headline_index: HeadlineIndex | None = None,
    client: httpx.Client | None = None,
    post_process: bool = True,
) -> ProcessResult:
    now = utcnow()
    fetch_log = SourceFetchLog(
        source_id=source.id,
        trigger=trigger,
        status=outcome.status,
        http_status=outcome.http_status,
        started_at=outcome.started_at,
        finished_at=now,
        duration_ms=outcome.duration_ms,
        bytes_received=len(outcome.content) if outcome.content else None,
        error_message=outcome.error,
    )
    result = ProcessResult(log=fetch_log)
    source.last_fetched_at = now
    source.last_http_status = outcome.http_status

    if outcome.status == "success":
        try:
            parsed = parse_feed(
                outcome.content or b"",
                content_type=outcome.content_type,
                source_type=source.source_type,
                api_config=source.api_config,
            )
        except FeedParseError as exc:
            outcome.status = "error"
            outcome.error = str(exc)
            fetch_log.status = "error"
            fetch_log.error_message = str(exc)
        else:
            if headline_index is None:
                cfg = get_setting(db, "dedup")
                headline_index = HeadlineIndex.load(db, window_start(now, cfg["headline_window_hours"]))
            new_items, dups, skipped = ingest_entries(
                db, source, parsed.entries, headline_index=headline_index, client=client
            )
            fetch_log.items_found = len(parsed.entries)
            fetch_log.items_new = len(new_items)
            fetch_log.items_duplicate = dups
            fetch_log.items_skipped = skipped
            notes = "; ".join(w for w in (outcome.note, parsed.warning) if w)
            if notes:
                fetch_log.error_message = notes[:2000]
            source.etag = (outcome.etag or None) and outcome.etag[:512]
            source.last_modified = (outcome.last_modified or None) and outcome.last_modified[:128]
            source.total_items = (source.total_items or 0) + len(new_items)
            result.new_items = new_items

    if outcome.status in ("success", "not_modified"):
        source.last_success_at = now
        source.consecutive_failures = 0
        source.last_error = None
    else:
        source.consecutive_failures = (source.consecutive_failures or 0) + 1
        source.last_error = outcome.error or "Unknown error"
        source.last_error_at = now
    _schedule_next(source, outcome, interval)
    db.add(fetch_log)
    db.flush()

    if post_process and result.new_items:
        from app.services.pipeline import post_process_items

        post_process_items(db, result.new_items)
    return result


def collect_source(
    db: Session,
    source: Source,
    *,
    trigger: str = "manual",
    client_factory: ClientFactory | None = None,
) -> ProcessResult:
    """Fetch and process one source synchronously (manual refresh, tests, CLI)."""
    factory = client_factory or make_client
    per_source, global_min = effective_intervals(db)
    with factory() as client:
        outcome = fetch_http(client, SourceSnapshot.of(source))
        result = process_outcome(
            db, source, outcome, trigger=trigger, interval=interval_for(source, per_source, global_min), client=client
        )
    db.commit()
    return result


def claim_due_sources(db: Session, limit: int) -> list[Source]:
    """Pick sources whose next fetch is due and lease them so other workers skip them."""
    now = utcnow()
    stmt = (
        select(Source)
        .where(
            Source.is_active.is_(True),
            Source.deleted_at.is_(None),
            (Source.next_fetch_at.is_(None)) | (Source.next_fetch_at <= now),
        )
        .order_by(Source.priority.asc(), Source.next_fetch_at.asc().nulls_first())
        .limit(limit)
    )
    if is_postgres(db):
        stmt = stmt.with_for_update(skip_locked=True, of=Source)
    sources = list(db.scalars(stmt).unique())
    lease = now + timedelta(minutes=10)
    for s in sources:
        s.next_fetch_at = lease
    db.commit()
    return sources


def run_collection_cycle(
    db: Session, *, client_factory: ClientFactory | None = None, limit: int | None = None
) -> dict:
    settings = get_settings()
    if not get_setting(db, "collection")["enabled"]:
        return {"sources": 0, "new_items": 0, "errors": 0, "skipped": "collection disabled"}
    sources = claim_due_sources(db, limit or settings.fetch_batch_size)
    if not sources:
        return {"sources": 0, "new_items": 0, "errors": 0}
    outcomes = fetch_many([SourceSnapshot.of(s) for s in sources], client_factory)
    by_id = {s.id: s for s in sources}
    per_source, global_min = effective_intervals(db)
    dedup_cfg = get_setting(db, "dedup")
    index = HeadlineIndex.load(db, window_start(utcnow(), dedup_cfg["headline_window_hours"]))
    stats = {"sources": len(sources), "new_items": 0, "errors": 0, "not_modified": 0}
    factory = client_factory or make_client
    with factory() as page_client:
        for outcome in outcomes:
            source = by_id[outcome.source_id]
            try:
                res = process_outcome(
                    db, source, outcome, trigger="scheduled",
                    interval=interval_for(source, per_source, global_min),
                    headline_index=index, client=page_client,
                )
                db.commit()
                stats["new_items"] += len(res.new_items)
                if res.log.status in ("error", "blocked"):
                    stats["errors"] += 1
                elif res.log.status == "not_modified":
                    stats["not_modified"] += 1
            except Exception:
                db.rollback()
                stats["errors"] += 1
                log.exception("Processing failed for source %s", outcome.source_id)
                src = db.get(Source, outcome.source_id)
                if src is not None:
                    src.consecutive_failures = (src.consecutive_failures or 0) + 1
                    src.last_error = "Internal processing error (see logs)"
                    src.last_error_at = utcnow()
                    src.next_fetch_at = utcnow() + timedelta(seconds=settings.min_poll_interval_seconds * 5)
                    db.commit()
    return stats


def probe_source_url(
    url: str,
    source_type: str = "rss",
    api_config: dict | None = None,
    respect_robots: bool = True,
    client_factory: ClientFactory | None = None,
) -> dict:
    """Connectivity + parse test without storing anything."""
    factory = client_factory or make_client
    snap = SourceSnapshot(uuid.uuid4(), url, None, None, respect_robots, source_type)
    with factory() as client:
        outcome = fetch_http(client, snap)
    result: dict = {
        "ok": False,
        "status": outcome.status,
        "http_status": outcome.http_status,
        "duration_ms": outcome.duration_ms,
        "error": outcome.error,
        "final_url": outcome.final_url,
        "etag": outcome.etag,
        "last_modified": outcome.last_modified,
        "feed_title": None,
        "feed_format": None,
        "language": None,
        "entries": 0,
        "sample": [],
    }
    if outcome.status != "success":
        return result
    try:
        parsed = parse_feed(outcome.content or b"", content_type=outcome.content_type, source_type=source_type, api_config=api_config)
    except FeedParseError as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        return result
    result.update(
        ok=True,
        feed_title=parsed.title,
        feed_format=parsed.feed_format,
        language=parsed.language,
        entries=len(parsed.entries),
        warning="; ".join(w for w in (outcome.note, parsed.warning) if w) or None,
        sample=[
            {
                "title": e.title,
                "url": e.url,
                "published_at": e.published_at.isoformat() if e.published_at else None,
                "has_summary": bool(e.summary),
                "has_image": bool(e.image_urls),
            }
            for e in parsed.entries[:5]
        ],
    )
    return result


# assignment_sources is imported for side effects in some call sites
__all__ = ["assignment_sources", "collect_source", "run_collection_cycle", "probe_source_url"]
