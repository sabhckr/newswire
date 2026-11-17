"""Server-side sessions. Only a SHA-256 of the session token is stored."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.models import User, UserSession

SESSION_COOKIE = "nw_session"
CSRF_COOKIE = "nw_csrf"
CSRF_HEADER = "x-csrf-token"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: User, ip: str | None, user_agent: str | None) -> tuple[str, UserSession]:
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    now = utcnow()
    sess = UserSession(
        user_id=user.id,
        token_hash=_hash(token),
        csrf_token=secrets.token_urlsafe(32),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(hours=settings.session_absolute_hours),
        ip=(ip or "")[:64] or None,
        user_agent=(user_agent or "")[:255] or None,
    )
    db.add(sess)
    db.flush()
    return token, sess


def resolve_session(db: Session, token: str | None) -> UserSession | None:
    if not token:
        return None
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == _hash(token)))
    if sess is None or sess.revoked_at is not None:
        return None
    now = utcnow()
    settings = get_settings()
    if sess.expires_at <= now:
        return None
    if sess.last_seen_at + timedelta(minutes=settings.session_idle_minutes) <= now:
        return None
    if not sess.user.is_active:
        return None
    # Write the activity timestamp at most once a minute to keep reads cheap.
    if (now - sess.last_seen_at).total_seconds() > 60:
        sess.last_seen_at = now
        db.commit()
    return sess


def revoke_session(db: Session, sess: UserSession) -> None:
    sess.revoked_at = utcnow()
    db.flush()


def revoke_all_for_user(db: Session, user_id, except_id=None) -> int:
    count = 0
    for s in db.scalars(select(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))):
        if except_id is not None and s.id == except_id:
            continue
        s.revoked_at = utcnow()
        count += 1
    db.flush()
    return count


def csrf_matches(expected: str, provided: str | None) -> bool:
    return bool(provided) and hmac.compare_digest(expected, provided or "")
