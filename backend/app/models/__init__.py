"""All ORM models; importing this package registers every table on Base.metadata."""

from app.models.articles import Article, ArticleImage, ArticleSource, Tag, article_assignments, article_tags
from app.models.assignments import (
    AssignmentKeyword,
    AssignmentMatch,
    EditorialAssignment,
    assignment_geographies,
    assignment_sources,
)
from app.models.clusters import StoryCluster, StoryClusterItem
from app.models.geography import GeographicEntity, NewsGeography
from app.models.images import ImageAsset, ImageLicense
from app.models.news import NewsItem, NewsItemSource
from app.models.publishing import ArticlePublication, PublishingDestination, PublishingJob
from app.models.sources import Category, Source, SourceFetchLog, source_categories
from app.models.system import AuditLog, Backup, ScheduledJob, SystemSetting
from app.models.users import Notification, Role, SearchHistory, User, UserSession, user_roles
from app.models.videos import VideoAsset

__all__ = [
    "Article", "ArticleImage", "ArticleSource", "Tag", "article_assignments", "article_tags",
    "AssignmentKeyword", "AssignmentMatch", "EditorialAssignment", "assignment_geographies",
    "assignment_sources", "StoryCluster", "StoryClusterItem", "GeographicEntity", "NewsGeography",
    "ImageAsset", "ImageLicense", "NewsItem", "NewsItemSource", "ArticlePublication",
    "PublishingDestination", "PublishingJob", "Category", "Source", "SourceFetchLog",
    "source_categories", "AuditLog", "Backup", "ScheduledJob", "SystemSetting", "Notification",
    "Role", "SearchHistory", "User", "UserSession", "user_roles", "VideoAsset",
]
