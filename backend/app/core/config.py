"""Environment-based configuration. Every setting can be overridden with a NEWSWIRE_* variable."""

from __future__ import annotations

import logging
import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent

VERSION = "0.10.0"

log = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWSWIRE_",
        env_file=(str(PROJECT_DIR / ".env"), str(BACKEND_DIR / ".env")),
        extra="ignore",
    )

    env: str = "development"
    database_url: str = "postgresql+psycopg://localhost/newswire"
    secret_key: str = ""
    storage_dir: Path = PROJECT_DIR / "storage"

    # Public base URL of this installation (used in built-in publishing links).
    public_base_url: str = "http://localhost:3000"

    # Sessions and cookies
    cookie_secure: bool = False
    session_idle_minutes: int = 60 * 12
    session_absolute_hours: int = 24 * 14

    # Outbound HTTP
    allow_private_network: bool = False
    fetch_concurrency: int = Field(default=4, ge=1, le=32)
    fetch_timeout_seconds: float = 20.0
    per_host_delay_seconds: float = 1.0
    max_feed_bytes: int = 5_000_000
    max_image_bytes: int = 15_000_000
    max_upload_bytes: int = 15_000_000
    # Video uploads for Facebook Reels, YouTube and websites (MP4 or MOV). The reverse proxy's
    # body limit for /api/v1/videos must be at least this large.
    max_video_bytes: int = 1_000_000_000
    min_poll_interval_seconds: int = 60
    default_poll_interval_seconds: int = 300
    max_backoff_seconds: int = 6 * 3600
    user_agent: str = "NEWSWIRE/0.10 (self-hosted news monitor)"
    # Optional contact string appended to the User-Agent (Wikimedia asks API clients for one).
    contact: str = ""

    # Scheduler
    embedded_scheduler: bool = False
    scheduler_tick_seconds: int = 30
    fetch_batch_size: int = 50

    # Image providers (optional credentials raise Openverse rate limits)
    openverse_client_id: str = ""
    openverse_client_secret: str = ""

    # Facebook Pages and YouTube publishing. The URLs only change for a new Graph API version
    # or for tests against a local stand-in.
    facebook_graph_version: str = "v23.0"
    facebook_graph_url: str = "https://graph.facebook.com"
    facebook_dialog_url: str = "https://www.facebook.com"
    facebook_upload_url: str = "https://rupload.facebook.com"
    facebook_scopes: str = "pages_show_list,pages_read_engagement,pages_manage_posts"
    google_auth_url: str = "https://accounts.google.com/o/oauth2/v2/auth"
    google_token_url: str = "https://oauth2.googleapis.com/token"
    youtube_api_url: str = "https://www.googleapis.com/youtube/v3"
    youtube_upload_url: str = "https://www.googleapis.com/upload/youtube/v3"

    # Backups
    backup_encryption_key: str = ""
    pg_dump_path: str = "pg_dump"
    pg_restore_path: str = "pg_restore"

    # Logging and limits
    log_level: str = "INFO"
    log_json: bool = True
    rate_limit_per_minute: int = 600
    login_rate_limit_per_minute: int = 10

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def images_dir(self) -> Path:
        return self.storage_dir / "images"

    @property
    def videos_dir(self) -> Path:
        # Under the media root, so /media/videos/... serves them like images.
        return self.images_dir / "videos"

    @property
    def backups_dir(self) -> Path:
        return self.storage_dir / "backups"

    @property
    def logs_dir(self) -> Path:
        return self.storage_dir / "logs"

    @property
    def full_user_agent(self) -> str:
        return f"{self.user_agent} {self.contact}".strip() if self.contact else self.user_agent


def _ensure_secret(settings: Settings) -> None:
    """Production must set NEWSWIRE_SECRET_KEY. Development falls back to a generated key file."""
    if settings.secret_key:
        return
    if settings.env == "production":
        raise RuntimeError("NEWSWIRE_SECRET_KEY must be set in production")
    key_file = settings.storage_dir / ".secret_key"
    key_file.parent.mkdir(parents=True, exist_ok=True)
    if key_file.exists():
        settings.secret_key = key_file.read_text().strip()
    else:
        settings.secret_key = secrets.token_urlsafe(48)
        key_file.write_text(settings.secret_key)
        os.chmod(key_file, 0o600)
        log.warning("NEWSWIRE_SECRET_KEY not set; generated a development key in %s", key_file)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    for d in (settings.storage_dir, settings.images_dir, settings.backups_dir, settings.logs_dir):
        d.mkdir(parents=True, exist_ok=True)
    _ensure_secret(settings)
    return settings
