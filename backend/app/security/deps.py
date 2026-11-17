"""FastAPI dependencies: current user, CSRF enforcement and permission checks."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError, forbidden
from app.models import User, UserSession
from app.security.permissions import permissions_for
from app.security.sessions import CSRF_HEADER, SESSION_COOKIE, csrf_matches, resolve_session

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass
class CurrentUser:
    user: User
    session: UserSession
    permissions: set[str]

    @property
    def id(self):
        return self.user.id

    @property
    def label(self) -> str:
        return self.user.email

    def can(self, perm: str) -> bool:
        return perm in self.permissions


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def get_current_user(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    sess = resolve_session(db, request.cookies.get(SESSION_COOKIE))
    if sess is None:
        raise AppError(401, "unauthorized", "Sign in required")
    if request.method not in SAFE_METHODS:
        if not csrf_matches(sess.csrf_token, request.headers.get(CSRF_HEADER)):
            raise AppError(403, "csrf_failed", "Missing or invalid CSRF token")
    user = sess.user
    return CurrentUser(user=user, session=sess, permissions=permissions_for(user.role_names))


def require(*perms: str):
    def _dep(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        missing = [p for p in perms if p not in current.permissions]
        if missing:
            raise forbidden(f"Requires permission: {', '.join(missing)}")
        return current

    return _dep
