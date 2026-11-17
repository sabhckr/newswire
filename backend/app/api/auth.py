from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps_limits import login_limiter
from app.core.config import get_settings
from app.core.db import get_db, utcnow
from app.core.errors import AppError
from app.models import Role, User
from app.schemas.domain import LoginIn, PasswordChangeIn, ProfileIn, SetupIn, UserOut
from app.security.deps import CurrentUser, client_ip, get_current_user
from app.security.passwords import DUMMY_HASH, hash_password, needs_rehash, password_problems, verify_password
from app.security.permissions import permissions_for
from app.security.sessions import CSRF_COOKIE, SESSION_COOKIE, create_session, revoke_all_for_user, revoke_session
from app.services.audit import record

router = APIRouter(prefix="/auth", tags=["auth"])

LOCK_AFTER = 8
LOCK_MINUTES = 15


def _session_payload(user: User, csrf: str) -> dict:
    return {
        "user": UserOut.model_validate(user).model_dump(mode="json"),
        "permissions": sorted(permissions_for(user.role_names)),
        "csrf_token": csrf,
    }


def _set_cookies(response: Response, token: str, csrf: str) -> None:
    s = get_settings()
    max_age = s.session_absolute_hours * 3600
    response.set_cookie(SESSION_COOKIE, token, max_age=max_age, httponly=True, secure=s.cookie_secure,
                        samesite="lax", path="/")
    response.set_cookie(CSRF_COOKIE, csrf, max_age=max_age, httponly=False, secure=s.cookie_secure,
                        samesite="lax", path="/")


@router.get("/setup-status")
def setup_status(db: Session = Depends(get_db)):
    return {"needs_setup": (db.scalar(select(func.count()).select_from(User)) or 0) == 0}


@router.post("/setup")
def first_run_setup(body: SetupIn, request: Request, response: Response, db: Session = Depends(get_db)):
    """Create the first administrator. Only possible while no user exists."""
    if (db.scalar(select(func.count()).select_from(User)) or 0) > 0:
        raise AppError(409, "already_setup", "Setup has already been completed")
    problems = password_problems(body.password)
    if problems:
        raise AppError(422, "weak_password", problems[0], problems)
    admin_role = db.scalar(select(Role).where(Role.name == "admin"))
    user = User(email=body.email.lower(), display_name=body.display_name, password_hash=hash_password(body.password),
                roles=[admin_role] if admin_role else [])
    db.add(user)
    db.flush()
    token, sess = create_session(db, user, client_ip(request), request.headers.get("user-agent"))
    user.last_login_at = utcnow()
    record(db, "auth.setup", f"Initial administrator {user.email} created", actor=user, entity_type="user",
           entity_id=user.id, ip=client_ip(request))
    db.commit()
    _set_cookies(response, token, sess.csrf_token)
    return _session_payload(user, sess.csrf_token)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = client_ip(request) or "unknown"
    allowed, retry = login_limiter.hit(f"login:{ip}")
    if not allowed:
        raise AppError(429, "rate_limited", f"Too many sign-in attempts. Try again in {int(retry) + 1} seconds.")
    email = body.email.strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    now = utcnow()
    if user is not None and user.locked_until and user.locked_until > now:
        raise AppError(429, "account_locked", "Too many failed attempts. The account is temporarily locked.")
    valid = verify_password(user.password_hash if user else DUMMY_HASH, body.password)
    if user is None or not valid or not user.is_active:
        if user is not None:
            user.failed_login_count = (user.failed_login_count or 0) + 1
            if user.failed_login_count >= LOCK_AFTER:
                user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
                user.failed_login_count = 0
            record(db, "auth.login_failed", f"Failed sign-in for {email}", entity_type="user", entity_id=user.id, ip=ip)
            db.commit()
        raise AppError(401, "invalid_credentials", "Email or password is incorrect")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    token, sess = create_session(db, user, ip, request.headers.get("user-agent"))
    record(db, "auth.login", f"{user.email} signed in", actor=user, entity_type="user", entity_id=user.id, ip=ip)
    db.commit()
    _set_cookies(response, token, sess.csrf_token)
    return _session_payload(user, sess.csrf_token)


@router.post("/logout")
def logout(response: Response, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    sess = db.merge(current.session)
    revoke_session(db, sess)
    record(db, "auth.logout", f"{current.label} signed out", actor=current, entity_type="user", entity_id=current.id)
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(current: CurrentUser = Depends(get_current_user)):
    return _session_payload(current.user, current.session.csrf_token)


@router.patch("/me")
def update_me(body: ProfileIn, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    user = db.merge(current.user)
    if body.display_name is not None:
        user.display_name = body.display_name
    if body.theme_preference is not None:
        user.theme_preference = body.theme_preference
    db.commit()
    return _session_payload(user, current.session.csrf_token)


@router.post("/me/password")
def change_password(body: PasswordChangeIn, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    user = db.merge(current.user)
    if not verify_password(user.password_hash, body.current_password):
        raise AppError(400, "invalid_password", "Current password is incorrect")
    problems = password_problems(body.new_password)
    if problems:
        raise AppError(422, "weak_password", problems[0], problems)
    user.password_hash = hash_password(body.new_password)
    revoked = revoke_all_for_user(db, user.id, except_id=current.session.id)
    record(db, "auth.password_changed", f"{user.email} changed their password ({revoked} other sessions signed out)",
           actor=current, entity_type="user", entity_id=user.id)
    db.commit()
    return {"ok": True, "other_sessions_revoked": revoked}
