"""Test configuration.

By default tests run against a temporary SQLite database. Set NEWSWIRE_TEST_DATABASE_URL to a
PostgreSQL URL (e.g. postgresql+psycopg://localhost/newswire_test) to run the same suite on
PostgreSQL; the schema is then built with Alembic migrations.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="newswire-test-"))
os.environ["NEWSWIRE_DATABASE_URL"] = os.environ.get("NEWSWIRE_TEST_DATABASE_URL", f"sqlite:///{_TMP / 'test.db'}")
os.environ["NEWSWIRE_STORAGE_DIR"] = str(_TMP / "storage")
os.environ["NEWSWIRE_SECRET_KEY"] = "test-secret-key-for-newswire-tests-only"
os.environ["NEWSWIRE_PER_HOST_DELAY_SECONDS"] = "0"
os.environ["NEWSWIRE_LOG_JSON"] = "false"
os.environ["NEWSWIRE_RATE_LIMIT_PER_MINUTE"] = "100000"
os.environ["NEWSWIRE_LOGIN_RATE_LIMIT_PER_MINUTE"] = "1000"

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

import app.models  # noqa: E402,F401
from app.collectors import robots  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import Base, SessionLocal, make_engine, set_engine  # noqa: E402
from app.geography.gazetteer import invalidate_gazetteer  # noqa: E402
from app.geography.seed import seed_bangladesh, seed_builtin  # noqa: E402

KEEP_TABLES = {"geographic_entities", "alembic_version"}


@pytest.fixture(scope="session")
def engine():
    settings = get_settings()
    eng = make_engine(settings.database_url)
    if eng.dialect.name == "postgresql":
        from alembic import command
        from alembic.config import Config

        with eng.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", settings.database_url)
        command.upgrade(cfg, "head")
    else:
        Base.metadata.create_all(eng)
    set_engine(eng)
    db = SessionLocal()
    seed_builtin(db)
    seed_bangladesh(db)
    db.commit()
    db.close()
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine):
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            names = ", ".join(t.name for t in Base.metadata.sorted_tables if t.name not in KEEP_TABLES)
            conn.execute(text(f"TRUNCATE {names} CASCADE"))
        else:
            for table in reversed(Base.metadata.sorted_tables):
                if table.name not in KEEP_TABLES:
                    conn.execute(table.delete())
        conn.execute(text("DELETE FROM geographic_entities WHERE data_source IS NULL OR data_source <> 'builtin'"))
    robots.clear_cache()
    invalidate_gazetteer()
    from app.api import deps_limits

    deps_limits.reset_limits()


class FeedServer:
    """Routes for httpx.MockTransport: url -> (status, headers, body) or a callable."""

    def __init__(self):
        self.routes: dict[str, object] = {}
        self.requests: list[httpx.Request] = []

    def add(self, url: str, body: bytes | str = b"", status: int = 200, headers: dict | None = None):
        if isinstance(body, str):
            body = body.encode()
        self.routes[url] = (status, headers or {"content-type": "application/rss+xml"}, body)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        route = self.routes.get(url)
        if route is None:
            if url.endswith("/robots.txt"):
                return httpx.Response(404, text="not found")
            return httpx.Response(404, text="no route")
        if callable(route):
            return route(request)
        status, headers, body = route
        return httpx.Response(status, headers=headers, content=body)

    def factory(self):
        def make():
            return httpx.Client(transport=httpx.MockTransport(self.handler), follow_redirects=False)

        return make


@pytest.fixture()
def feeds():
    robots.clear_cache()  # each test's fake sites have their own robots.txt
    from app.collectors import ingest

    ingest._lookup_misses.clear()
    return FeedServer()


def rss(items: list[dict], title: str = "Test Feed", language: str = "en") -> str:
    parts = []
    for it in items:
        extra = ""
        if it.get("category"):
            extra += f"<category>{it['category']}</category>"
        if it.get("image"):
            extra += f'<enclosure url="{it["image"]}" type="image/jpeg" length="1"/>'
        pub = f"<pubDate>{it['pubDate']}</pubDate>" if it.get("pubDate") else ""
        parts.append(
            f"<item><title>{it['title']}</title><link>{it['link']}</link>"
            f"<guid>{it.get('guid', it['link'])}</guid>{pub}"
            f"<description>{it.get('description', '')}</description>{extra}</item>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        f"<title>{title}</title><link>https://example.com</link><language>{language}</language>"
        + "".join(parts)
        + "</channel></rss>"
    )
