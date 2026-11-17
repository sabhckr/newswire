"""Request/response schemas for the main domain objects."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.common import ORM, CategoryOut, GeoEntityOut

# --- users ---------------------------------------------------------------------------------


class UserOut(ORM):
    id: uuid.UUID
    email: str
    display_name: str
    is_active: bool
    theme_preference: str
    role_names: list[str]
    last_login_at: datetime | None
    created_at: datetime


class SetupIn(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=10, max_length=256)


class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)


class ProfileIn(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    theme_preference: Literal["light", "dark", "system"] | None = None


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=256)


class UserCreateIn(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=10, max_length=256)
    roles: list[Literal["admin", "editor", "researcher", "viewer"]] = ["viewer"]


class UserUpdateIn(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    roles: list[Literal["admin", "editor", "researcher", "viewer"]] | None = None
    is_active: bool | None = None


class PasswordResetIn(BaseModel):
    password: str = Field(min_length=10, max_length=256)


# --- sources -------------------------------------------------------------------------------

SourceType = Literal["rss", "atom", "json", "sitemap"]


class SourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    publisher: str = Field(min_length=1, max_length=200)
    source_type: SourceType = "rss"
    feed_url: str = Field(min_length=8, max_length=2048)
    website_url: str | None = Field(default=None, max_length=2048)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    region: str | None = Field(default=None, max_length=120)
    city: str | None = Field(default=None, max_length=120)
    language: str | None = Field(default=None, max_length=16)
    category_ids: list[uuid.UUID] = []
    polling_interval_seconds: int = Field(default=300, ge=60, le=7 * 86400)
    priority: int = Field(default=3, ge=1, le=5)
    reliability_notes: str | None = None
    terms_notes: str | None = None
    is_active: bool = True
    respect_robots: bool = True
    extract_page_metadata: bool = False
    api_config: dict[str, Any] | None = None

    @field_validator("country_code")
    @classmethod
    def upper_cc(cls, v):
        return v.upper() if v else v

    @field_validator("language")
    @classmethod
    def lower_lang(cls, v):
        return v.lower() if v else v


class SourceOut(ORM):
    id: uuid.UUID
    name: str
    publisher: str
    source_type: str
    feed_url: str
    website_url: str | None
    country_code: str | None
    region: str | None
    city: str | None
    language: str | None
    categories: list[CategoryOut]
    polling_interval_seconds: int
    priority: int
    reliability_notes: str | None
    terms_notes: str | None
    is_active: bool
    respect_robots: bool
    extract_page_metadata: bool
    api_config: dict | None
    last_fetched_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    last_error_at: datetime | None
    last_http_status: int | None
    etag: str | None
    last_modified: str | None
    consecutive_failures: int
    next_fetch_at: datetime | None
    total_items: int
    health: str
    created_at: datetime
    updated_at: datetime


class SourceDiscoverIn(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    respect_robots: bool = True


class SourceTestIn(BaseModel):
    feed_url: str = Field(min_length=8, max_length=2048)
    source_type: SourceType = "rss"
    api_config: dict[str, Any] | None = None
    respect_robots: bool = True


class FetchLogOut(ORM):
    id: uuid.UUID
    source_id: uuid.UUID
    trigger: str
    status: str
    http_status: int | None
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    items_found: int
    items_new: int
    items_duplicate: int
    items_skipped: int
    bytes_received: int | None
    error_message: str | None


class BulkIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    action: str


# --- news ----------------------------------------------------------------------------------


class NewsGeoOut(ORM):
    id: uuid.UUID
    entity_id: uuid.UUID | None
    role: str
    method: str
    confidence: float
    original_text: str | None
    country_code: str | None
    region: str | None
    city: str | None
    entity: GeoEntityOut | None = None


class NewsItemOut(ORM):
    id: uuid.UUID
    title: str
    url: str
    canonical_url: str | None
    source_id: uuid.UUID | None
    publisher: str | None = None
    source_name: str | None = None
    summary: str | None
    author: str | None
    language: str | None
    category_text: str | None
    image_urls: list | None
    published_at: datetime | None
    retrieved_at: datetime
    sort_at: datetime
    is_duplicate: bool
    duplicate_of_id: uuid.UUID | None
    duplicate_reason: str | None
    review_status: str
    match_count: int = 0
    cluster_id: uuid.UUID | None = None
    countries: list[str] = []


class NewsReviewIn(BaseModel):
    review_status: Literal["unreviewed", "verified", "archived"]


class EventLocationIn(BaseModel):
    entity_id: uuid.UUID
    role: Literal["event", "article"] = "event"


# --- assignments ---------------------------------------------------------------------------

KeywordKind = Literal["primary", "secondary", "synonym", "required", "excluded"]


class KeywordIn(BaseModel):
    term: str = Field(min_length=1, max_length=200)
    kind: KeywordKind
    weight: float | None = Field(default=None, ge=-200, le=200)


class KeywordOut(ORM):
    id: uuid.UUID
    term: str
    kind: str
    weight: float | None


class AssignmentIn(BaseModel):
    headline: str = Field(min_length=3, max_length=500)
    description: str | None = None
    keywords: list[KeywordIn] = []
    category_id: uuid.UUID | None = None
    language: str | None = Field(default=None, max_length=16)
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    status: Literal["active", "paused"] = "active"
    start_date: date | None = None
    end_date: date | None = None
    monitoring_interval_seconds: int | None = Field(default=None, ge=60, le=86400)
    min_score: float = Field(default=25.0, ge=0, le=1000)
    restrict_to_sources: bool = False
    geo_mode: Literal["boost", "require"] = "boost"
    city: str | None = Field(default=None, max_length=200)
    source_ids: list[uuid.UUID] = []
    geography_ids: list[uuid.UUID] = []

    @field_validator("end_date")
    @classmethod
    def end_after_start(cls, v, info):
        start = info.data.get("start_date")
        if v and start and v < start:
            raise ValueError("end_date must be on or after start_date")
        return v


class SourceBrief(ORM):
    id: uuid.UUID
    name: str
    publisher: str


class AssignmentOut(ORM):
    id: uuid.UUID
    headline: str
    description: str | None
    keywords: list[KeywordOut]
    category: CategoryOut | None
    category_id: uuid.UUID | None
    language: str | None
    priority: str
    status: str
    start_date: date | None
    end_date: date | None
    monitoring_interval_seconds: int | None
    min_score: float
    restrict_to_sources: bool
    geo_mode: str
    city: str | None
    sources: list[SourceBrief]
    geographies: list[GeoEntityOut]
    research_status: str
    editorial_notes: str | None
    source_checklist: list | None
    last_matched_at: datetime | None
    match_count: int
    unreviewed_count: int = 0
    created_at: datetime
    updated_at: datetime


class MatchOut(ORM):
    id: uuid.UUID
    assignment_id: uuid.UUID
    news_item_id: uuid.UUID
    score: float
    breakdown: list | None
    explanation: str
    matched_at: datetime
    review_status: str
    reviewed_at: datetime | None
    notes: str | None
    news_item: NewsItemOut


class MatchReviewIn(BaseModel):
    review_status: Literal["unreviewed", "verified", "rejected", "archived"] | None = None
    notes: str | None = None


class MatchBulkIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    review_status: Literal["unreviewed", "verified", "rejected", "archived"]


class ResearchUpdateIn(BaseModel):
    editorial_notes: str | None = None
    source_checklist: list[dict[str, Any]] | None = None
    research_status: Literal["open", "in_progress", "done"] | None = None


# --- clusters ------------------------------------------------------------------------------


class ClusterOut(ORM):
    id: uuid.UUID
    title: str
    title_source: str
    representative_item_id: uuid.UUID | None
    first_seen_at: datetime
    last_seen_at: datetime
    item_count: int
    publisher_count: int
    is_locked: bool
    status: str
    notes: str | None
    publishers: list[str] = []


class ClusterUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    notes: str | None = None
    is_locked: bool | None = None
    status: Literal["active", "archived"] | None = None
    reset_title: bool = False


class ClusterMergeIn(BaseModel):
    cluster_ids: list[uuid.UUID] = Field(min_length=1)


class ClusterSplitIn(BaseModel):
    item_ids: list[uuid.UUID] = Field(min_length=1)
    title: str | None = None


class ClusterItemIn(BaseModel):
    news_item_id: uuid.UUID


# --- images --------------------------------------------------------------------------------


class LicenseOut(ORM):
    id: uuid.UUID
    code: str
    version: str
    name: str
    url: str | None
    allows_commercial: bool | None
    allows_derivatives: bool | None
    requires_attribution: bool | None
    share_alike: bool
    is_known: bool


class ImageOut(ORM):
    id: uuid.UUID
    provider: str
    provider_id: str
    repository: str | None
    title: str | None
    creator: str | None
    creator_url: str | None
    source_page_url: str | None
    original_url: str | None
    thumbnail_url: str | None
    width: int | None
    height: int | None
    mime: str | None
    license: LicenseOut | None
    license_raw: str | None
    license_url: str | None
    attribution_text: str | None
    retrieved_at: datetime
    download_status: str
    download_error: str | None
    local_url: str | None = None
    local_thumbnail_url: str | None = None
    approval_status: str
    approved_at: datetime | None
    notes: str | None
    assignment_id: uuid.UUID | None
    search_query: str | None
    warnings: list[dict] = []
    created_at: datetime


class ImageCandidateIn(BaseModel):
    provider: Literal["openverse", "wikimedia"]
    provider_id: str
    title: str | None = None
    creator: str | None = None
    creator_url: str | None = None
    source_page_url: str | None = None
    original_url: str | None = None
    thumbnail_url: str | None = None
    width: int | None = None
    height: int | None = None
    mime: str | None = None
    license_code: str
    license_version: str = ""
    license_raw: str | None = None
    license_url: str | None = None
    attribution_text: str | None = None
    repository: str | None = None
    metadata: dict[str, Any] = {}


class ImageSaveIn(BaseModel):
    candidate: ImageCandidateIn
    assignment_id: uuid.UUID | None = None
    query: str | None = None


class ImageUpdateIn(BaseModel):
    title: str | None = None
    creator: str | None = None
    attribution_text: str | None = None
    notes: str | None = None
    assignment_id: uuid.UUID | None = None


class ImageRejectIn(BaseModel):
    note: str | None = None


# --- articles ------------------------------------------------------------------------------


class ArticleSourceIn(BaseModel):
    news_item_id: uuid.UUID | None = None
    url: str | None = Field(default=None, max_length=2048)
    title: str | None = None
    publisher: str | None = None
    note: str | None = None


class ArticleImageIn(BaseModel):
    image_asset_id: uuid.UUID
    caption: str | None = None
    is_modified: bool = False


class ArticleIn(BaseModel):
    headline: str | None = Field(default=None, max_length=500)
    subtitle: str | None = Field(default=None, max_length=500)
    slug: str | None = Field(default=None, max_length=255)
    body_html: str | None = None
    body_json: dict | None = None
    excerpt: str | None = None
    seo_title: str | None = Field(default=None, max_length=255)
    seo_description: str | None = Field(default=None, max_length=500)
    category_id: uuid.UUID | None = None
    featured_image_id: uuid.UUID | None = None
    featured_image_caption: str | None = None
    geo_entity_ids: list[uuid.UUID] | None = None
    tags: list[str] | None = None
    sources: list[ArticleSourceIn] | None = None
    images: list[ArticleImageIn] | None = None
    assignment_ids: list[uuid.UUID] | None = None
    version: int | None = None


class ArticleSourceOut(ORM):
    id: uuid.UUID
    news_item_id: uuid.UUID | None
    url: str
    title: str | None
    publisher: str | None
    note: str | None


class ArticleImageOut(ORM):
    id: uuid.UUID
    image_asset_id: uuid.UUID
    caption: str | None
    position: int
    is_modified: bool
    image: ImageOut


class TagOut(ORM):
    id: uuid.UUID
    name: str
    slug: str


class PublicationOut(ORM):
    id: uuid.UUID
    destination_id: uuid.UUID
    destination_name: str | None = None
    destination_kind: str | None = None
    remote_id: str | None
    remote_url: str | None
    remote_status: str
    published_version: int | None
    last_synced_at: datetime | None
    sync_error: str | None


class ArticleListOut(ORM):
    id: uuid.UUID
    headline: str
    subtitle: str | None
    slug: str
    status: str
    category: CategoryOut | None
    word_count: int
    version: int
    last_published_version: int | None
    scheduled_at: datetime | None
    published_at: datetime | None
    author_id: uuid.UUID | None
    author_name: str | None = None
    featured_thumbnail: str | None = None
    created_at: datetime
    updated_at: datetime


class ArticleOut(ArticleListOut):
    body_html: str
    body_json: dict | None
    excerpt: str | None
    seo_title: str | None
    seo_description: str | None
    category_id: uuid.UUID | None
    featured_image_id: uuid.UUID | None
    featured_image: ImageOut | None
    featured_image_caption: str | None
    geo_entity_ids: list | None
    approved_at: datetime | None
    tags: list[TagOut]
    sources: list[ArticleSourceOut]
    images: list[ArticleImageOut]
    assignment_ids: list[uuid.UUID] = []
    transitions: list[str] = []
    checks: list[dict] = []
    publications: list[PublicationOut] = []
    has_unpublished_changes: bool = False


class TransitionIn(BaseModel):
    target: Literal["draft", "in_review", "approved", "scheduled", "published", "unpublished", "archived"]
    scheduled_at: datetime | None = None


class PublishIn(BaseModel):
    destination_id: uuid.UUID
    mode: Literal["immediate", "draft", "scheduled"] = "immediate"
    scheduled_for: datetime | None = None


class UnpublishIn(BaseModel):
    destination_id: uuid.UUID


# --- publishing ----------------------------------------------------------------------------


class DestinationIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["wordpress", "webhook"]
    base_url: str = Field(min_length=8, max_length=2048)
    username: str | None = Field(default=None, max_length=200)
    secret: str | None = Field(default=None, max_length=500, description="Write-only")
    is_active: bool = True
    is_commercial: bool = True
    auto_publish: bool = False


class DestinationUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, min_length=8, max_length=2048)
    username: str | None = None
    secret: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None
    is_commercial: bool | None = None
    auto_publish: bool | None = None


class DestinationOut(ORM):
    id: uuid.UUID
    name: str
    kind: str
    base_url: str | None
    username: str | None
    has_secret: bool = False
    is_active: bool
    is_commercial: bool
    auto_publish: bool
    last_checked_at: datetime | None
    last_check_status: str | None
    created_at: datetime


class JobOut(ORM):
    id: uuid.UUID
    article_id: uuid.UUID
    article_headline: str | None = None
    destination_id: uuid.UUID
    destination_name: str | None = None
    action: str
    status: str
    article_version: int
    attempts: int
    max_attempts: int
    scheduled_for: datetime | None
    next_attempt_at: datetime | None
    last_error: str | None
    remote_id: str | None
    remote_url: str | None
    completed_at: datetime | None
    created_at: datetime
