"""uploaded videos and per-destination publishing options

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-17 12:00:00+00:00

Adds the video_assets table, articles.video_id and publishing_jobs.options. Only adds tables
and nullable columns; no data is changed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import app.core.db

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "video_assets",
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("local_path", sa.String(length=500), nullable=False),
        sa.Column("mime", sa.String(length=64), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("uploaded_by_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", app.core.db.UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", app.core.db.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["uploaded_by_id"], ["users.id"],
                                name=op.f("fk_video_assets_uploaded_by_id_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_video_assets")),
    )
    op.create_index(op.f("ix_video_assets_file_sha256"), "video_assets", ["file_sha256"], unique=False)
    with op.batch_alter_table("articles") as batch:
        batch.add_column(sa.Column("video_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(op.f("fk_articles_video_id_video_assets"), "video_assets", ["video_id"], ["id"],
                                 ondelete="SET NULL")
    with op.batch_alter_table("publishing_jobs") as batch:
        batch.add_column(sa.Column("options", JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("publishing_jobs") as batch:
        batch.drop_column("options")
    with op.batch_alter_table("articles") as batch:
        batch.drop_constraint(op.f("fk_articles_video_id_video_assets"), type_="foreignkey")
        batch.drop_column("video_id")
    op.drop_index(op.f("ix_video_assets_file_sha256"), table_name="video_assets")
    op.drop_table("video_assets")
