"""The Alembic migrations build exactly the schema the models describe, and 0002 can be undone."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.core.db import Base

BACKEND = Path(__file__).resolve().parents[1]


def _config(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def test_migrations_match_models(tmp_path):
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    command.upgrade(_config(url), "head")
    engine = create_engine(url)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True, "render_as_batch": True})
        diff = [d for d in compare_metadata(ctx, Base.metadata)
                if not (d[0] in ("add_index", "remove_index") and d[1].name == "ix_news_items_fts")]
    assert diff == []
    cols = {c["name"] for c in inspect(engine).get_columns("articles")}
    assert "video_id" in cols and inspect(engine).has_table("video_assets")

    command.downgrade(_config(url), "0001")
    engine.dispose()
    engine = create_engine(url)
    assert not inspect(engine).has_table("video_assets")
    assert "options" not in {c["name"] for c in inspect(engine).get_columns("publishing_jobs")}
    engine.dispose()
