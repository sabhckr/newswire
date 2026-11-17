from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog, Notification


def record(
    db: Session,
    action: str,
    summary: str,
    *,
    actor=None,
    entity_type: str | None = None,
    entity_id: Any = None,
    details: dict | None = None,
    ip: str | None = None,
) -> AuditLog:
    """Append an audit entry. `actor` may be a CurrentUser, a User, or None for the system."""
    actor_id = None
    label = "system"
    if actor is not None:
        user = getattr(actor, "user", actor)
        actor_id = getattr(user, "id", None)
        label = getattr(user, "email", "system")
    entry = AuditLog(
        actor_id=actor_id,
        actor_label=label,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        summary=summary[:500],
        details=details,
        ip=ip,
    )
    db.add(entry)
    return entry


def notify(
    db: Session,
    kind: str,
    title: str,
    body: str | None = None,
    link: str | None = None,
    user_id=None,
) -> Notification:
    n = Notification(kind=kind, title=title[:255], body=body, link=link, user_id=user_id)
    db.add(n)
    return n
