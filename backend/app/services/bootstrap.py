"""Idempotent first-run setup: roles, categories, gazetteer, built-in destination, job rows."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.text import slugify
from app.geography.seed import seed_builtin
from app.models import Category, Role, ScheduledJob
from app.publishing.service import ensure_builtin_destination
from app.security.permissions import ROLE_DESCRIPTIONS

DEFAULT_CATEGORIES = [
    "World", "Politics", "Business", "Economy", "Technology", "Science", "Health", "Environment",
    "Climate", "Infrastructure", "Transport", "Energy", "Education", "Crime & Justice",
    "Conflict & Security", "Disaster", "Sports", "Culture", "Opinion",
]

JOB_DEFINITIONS = {
    "collection": "Poll news sources that are due",
    "publishing": "Process the publishing queue and scheduled publications",
    "backups": "Create scheduled backups and apply retention",
    "housekeeping": "Expire sessions and prune old fetch logs and notifications",
    "publication_sync": "Synchronise remote publication status",
}


def bootstrap(db: Session) -> None:
    for name, desc in ROLE_DESCRIPTIONS.items():
        if db.scalar(select(Role).where(Role.name == name)) is None:
            db.add(Role(name=name, description=desc))
    if db.scalar(select(Category.id).limit(1)) is None:
        for name in DEFAULT_CATEGORIES:
            db.add(Category(name=name, slug=slugify(name)))
    for name, desc in JOB_DEFINITIONS.items():
        if db.get(ScheduledJob, name) is None:
            db.add(ScheduledJob(name=name, description=desc))
    db.flush()
    seed_builtin(db)
    ensure_builtin_destination(db)
    db.commit()
