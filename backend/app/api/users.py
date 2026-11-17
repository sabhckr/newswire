from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.common import get_or_404
from app.core.db import get_db
from app.core.errors import AppError, conflict
from app.models import Role, User
from app.schemas.domain import PasswordResetIn, UserCreateIn, UserOut, UserUpdateIn
from app.security import permissions as P
from app.security.deps import CurrentUser, require
from app.security.passwords import hash_password, password_problems
from app.security.sessions import revoke_all_for_user
from app.services.audit import record

router = APIRouter(prefix="/users", tags=["users"])


def _roles(db: Session, names: list[str]) -> list[Role]:
    return list(db.scalars(select(Role).where(Role.name.in_(names))))


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.USERS_MANAGE))):
    return list(db.scalars(select(User).order_by(User.created_at)))


@router.get("/roles")
def list_roles(_: CurrentUser = Depends(require(P.READ))):
    return [{"name": n, "description": d, "permissions": sorted(P.ROLE_PERMISSIONS[n])} for n, d in P.ROLE_DESCRIPTIONS.items()]


@router.post("", response_model=UserOut, status_code=201)
def create_user(body: UserCreateIn, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.USERS_MANAGE))):
    if db.scalar(select(User).where(User.email == body.email.lower())):
        raise conflict("A user with this email already exists")
    problems = password_problems(body.password)
    if problems:
        raise AppError(422, "weak_password", problems[0], problems)
    user = User(email=body.email.lower(), display_name=body.display_name, password_hash=hash_password(body.password),
                roles=_roles(db, body.roles))
    db.add(user)
    db.flush()
    record(db, "user.created", f"User {user.email} created with roles {', '.join(body.roles)}", actor=current,
           entity_type="user", entity_id=user.id)
    db.commit()
    return user


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: uuid.UUID, body: UserUpdateIn, db: Session = Depends(get_db),
                current: CurrentUser = Depends(require(P.USERS_MANAGE))):
    user = get_or_404(db, User, user_id, "User")
    changes = []
    if body.display_name is not None:
        user.display_name = body.display_name
        changes.append("name")
    if body.roles is not None:
        if user.id == current.id and "admin" not in body.roles:
            raise AppError(409, "conflict", "You cannot remove your own admin role")
        if "admin" not in body.roles and "admin" in user.role_names:
            admins = db.scalar(select(func.count()).select_from(User).join(User.roles).where(Role.name == "admin", User.is_active.is_(True)))
            if (admins or 0) <= 1:
                raise AppError(409, "conflict", "At least one active administrator is required")
        user.roles = _roles(db, body.roles)
        changes.append(f"roles={','.join(body.roles)}")
    if body.is_active is not None:
        if user.id == current.id and not body.is_active:
            raise AppError(409, "conflict", "You cannot deactivate yourself")
        user.is_active = body.is_active
        if not body.is_active:
            revoke_all_for_user(db, user.id)
        changes.append("active" if body.is_active else "deactivated")
    record(db, "user.updated", f"User {user.email} updated: {'; '.join(changes)}", actor=current, entity_type="user",
           entity_id=user.id)
    db.commit()
    return user


@router.post("/{user_id}/reset-password")
def reset_password(user_id: uuid.UUID, body: PasswordResetIn, db: Session = Depends(get_db),
                   current: CurrentUser = Depends(require(P.USERS_MANAGE))):
    user = get_or_404(db, User, user_id, "User")
    problems = password_problems(body.password)
    if problems:
        raise AppError(422, "weak_password", problems[0], problems)
    user.password_hash = hash_password(body.password)
    revoke_all_for_user(db, user.id)
    record(db, "user.password_reset", f"Password reset for {user.email}", actor=current, entity_type="user", entity_id=user.id)
    db.commit()
    return {"ok": True}
