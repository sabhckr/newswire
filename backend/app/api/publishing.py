from __future__ import annotations

import uuid
from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.common import get_or_404
from app.core.crypto import encrypt_secret
from app.core.db import get_db, utcnow
from app.core.errors import AppError
from app.core.http import UnsafeURLError, make_client, validate_url
from app.core.pagination import PageParams
from app.models import Article, ArticlePublication, PublishingDestination, PublishingJob, User
from app.publishing.connectors import PublishError, connector_for
from app.publishing import social
from app.publishing.service import cancel_job, retry_job, run_publishing_cycle, sync_publication
from app.schemas.domain import (
    ConnectionOut,
    DestinationIn,
    DestinationOut,
    DestinationUpdateIn,
    FacebookPageIn,
    JobOut,
    PublicationOut,
    SocialPageOut,
)
from app.security import permissions as P
from app.security.deps import CurrentUser, require
from app.services.audit import record

router = APIRouter(prefix="/publishing", tags=["publishing"])


def dest_out(d: PublishingDestination) -> DestinationOut:
    o = DestinationOut.model_validate(d)
    o.has_secret = bool(d.secret_encrypted)
    if d.kind in social.SOCIAL_KINDS:
        cfg = d.config or {}
        o.has_secret = bool(social.load_secrets(d).get("client_secret"))
        connected = social.is_connected(d)
        if d.kind == "facebook":
            name = cfg.get("page_name") if connected else None
            url = f"https://www.facebook.com/{cfg['page_id']}" if connected and cfg.get("page_id") else None
        else:
            name = cfg.get("channel_title") if connected else None
            url = f"https://www.youtube.com/channel/{cfg['channel_id']}" if connected and cfg.get("channel_id") else None
        o.connection = ConnectionOut(
            connected=connected, account_name=name, account_url=url, client_id=cfg.get("client_id"),
            login_config_id=cfg.get("login_config_id"), redirect_uri=social.redirect_uri(),
            connected_at=cfg.get("connected_at") if connected else None,
            pages=[SocialPageOut(id=str(pg["id"]), name=str(pg["name"])) for pg in cfg.get("pages") or []],
        )
    return o


def publication_out(p: ArticlePublication) -> PublicationOut:
    return PublicationOut.model_validate(p).model_copy(update={
        "destination_name": p.destination.name, "destination_kind": p.destination.kind,
        "warning": (p.details or {}).get("warning") or ((p.details or {}).get("error") if p.remote_status == "failed" else None),
    })


def _check(url: str) -> None:
    try:
        validate_url(url)
    except UnsafeURLError as exc:
        raise AppError(422, "unsafe_url", str(exc)) from exc


@router.get("/destinations", response_model=list[DestinationOut])
def destinations(db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.READ))):
    return [dest_out(d) for d in db.scalars(select(PublishingDestination).order_by(PublishingDestination.created_at))]


@router.post("/destinations", response_model=DestinationOut, status_code=201)
def create_destination(body: DestinationIn, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.PUBLISHING_MANAGE))):
    if body.kind in social.SOCIAL_KINDS:
        client_id = (body.client_id or "").strip()
        secret = (body.secret or "").strip()
        if not client_id or not secret:
            what = "an App ID and an App secret" if body.kind == "facebook" else "a Client ID and a Client secret"
            raise AppError(422, "validation_error", f"{body.kind.title() if body.kind == 'facebook' else 'YouTube'} needs {what}")
        d = PublishingDestination(name=body.name, kind=body.kind, is_active=body.is_active,
                                  is_commercial=body.is_commercial, auto_publish=body.auto_publish)
        social.set_config(d, client_id=client_id, login_config_id=(body.login_config_id or "").strip() or None)
        social.save_secrets(d, {"client_secret": secret})
    else:
        if not body.base_url or len(body.base_url) < 8:
            raise AppError(422, "validation_error", "Enter the website address (https://…)")
        _check(body.base_url)
        if body.kind == "wordpress" and not (body.username and body.secret):
            raise AppError(422, "validation_error", "WordPress needs a username and an application password")
        d = PublishingDestination(name=body.name, kind=body.kind, base_url=body.base_url.rstrip("/"), username=body.username,
                                  secret_encrypted=encrypt_secret(body.secret) if body.secret else None,
                                  is_active=body.is_active, is_commercial=body.is_commercial, auto_publish=body.auto_publish)
    db.add(d)
    db.flush()
    record(db, "destination.created", f"Publishing destination {d.name} ({d.kind}) added", actor=current,
           entity_type="destination", entity_id=d.id)
    db.commit()
    return dest_out(d)


@router.put("/destinations/{dest_id}", response_model=DestinationOut)
def update_destination(dest_id: uuid.UUID, body: DestinationUpdateIn, db: Session = Depends(get_db),
                       current: CurrentUser = Depends(require(P.PUBLISHING_MANAGE))):
    d = get_or_404(db, PublishingDestination, dest_id, "Destination")
    data = body.model_dump(exclude_unset=True)
    if d.kind == "builtin":
        data = {k: v for k, v in data.items() if k in ("name", "is_active", "is_commercial", "auto_publish")}
    if d.kind in social.SOCIAL_KINDS:
        secret = (data.pop("secret", None) or "").strip()
        cfg = d.config or {}
        client_id = (data.pop("client_id", None) or "").strip()
        login_config_id = data.pop("login_config_id", None)
        data = {k: v for k, v in data.items() if k in ("name", "is_active", "is_commercial", "auto_publish")}
        for k, v in data.items():
            setattr(d, k, v)
        changed_app = bool(client_id and client_id != cfg.get("client_id"))
        if changed_app:
            # Tokens belong to the old app; the Page or channel must be connected again.
            social.clear_connection(d)
            social.set_config(d, client_id=client_id)
        if login_config_id is not None:
            social.set_config(d, login_config_id=login_config_id.strip() or None)
        if secret:
            sec = social.load_secrets(d)
            sec["client_secret"] = secret
            social.save_secrets(d, sec)
        record(db, "destination.updated", f"Publishing destination {d.name} updated"
               + (" (app changed; connect again)" if changed_app else "") + (" (secret replaced)" if secret else ""),
               actor=current, entity_type="destination", entity_id=d.id, details={"fields": sorted(data)})
        db.commit()
        return dest_out(d)
    data.pop("client_id", None)
    data.pop("login_config_id", None)
    if "base_url" in data and data["base_url"]:
        _check(data["base_url"])
        data["base_url"] = data["base_url"].rstrip("/")
    secret = data.pop("secret", None)
    for k, v in data.items():
        setattr(d, k, v)
    if secret:
        d.secret_encrypted = encrypt_secret(secret)
    record(db, "destination.updated", f"Publishing destination {d.name} updated" + (" (credential replaced)" if secret else ""),
           actor=current, entity_type="destination", entity_id=d.id, details={"fields": sorted(data)})
    db.commit()
    return dest_out(d)


@router.delete("/destinations/{dest_id}")
def delete_destination(dest_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.PUBLISHING_MANAGE))):
    d = get_or_404(db, PublishingDestination, dest_id, "Destination")
    if d.kind == "builtin":
        raise AppError(409, "conflict", "The built-in destination cannot be deleted; disable it instead")
    live = db.scalar(select(func.count()).select_from(ArticlePublication).where(
        ArticlePublication.destination_id == d.id, ArticlePublication.remote_status == "published")) or 0
    if live:
        raise AppError(409, "conflict", f"{live} articles are published here; unpublish them or disable the destination")
    record(db, "destination.deleted", f"Publishing destination {d.name} deleted", actor=current, entity_type="destination", entity_id=d.id)
    db.delete(d)
    db.commit()
    return {"ok": True}


@router.post("/destinations/{dest_id}/test")
def test_destination(dest_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.PUBLISHING_MANAGE))):
    d = get_or_404(db, PublishingDestination, dest_id, "Destination")
    ok, message = True, ""
    try:
        with make_client(timeout=20) as client:
            message = connector_for(d).check(client)
    except PublishError as exc:
        ok, message = False, str(exc)
    except Exception as exc:  # noqa: BLE001
        from app.core.http import describe_error

        ok, message = False, describe_error(exc)
    d.last_checked_at = utcnow()
    d.last_check_status = ("OK: " if ok else "FAILED: ") + message[:280]
    record(db, "destination.tested", f"Connection test for {d.name}: {'ok' if ok else 'failed'}", actor=current,
           entity_type="destination", entity_id=d.id)
    db.commit()
    return {"ok": ok, "message": message}


def _settings_redirect(**params) -> RedirectResponse:
    return RedirectResponse("/publishing?" + urlencode({"tab": "destinations", **params}), status_code=303)


@router.get("/oauth/info")
def oauth_info(_: CurrentUser = Depends(require(P.READ))):
    """The redirect address to register with Facebook and Google (it follows NEWSWIRE_PUBLIC_BASE_URL)."""
    return {"redirect_uri": social.redirect_uri()}


@router.post("/destinations/{dest_id}/connect")
def connect(dest_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.PUBLISHING_MANAGE))):
    """Start signing in to Facebook or Google. The browser goes to the returned address."""
    d = get_or_404(db, PublishingDestination, dest_id, "Destination")
    if d.kind not in social.SOCIAL_KINDS:
        raise AppError(400, "bad_request", "Only Facebook and YouTube destinations are connected this way")
    try:
        url = social.authorize_url(d, social.make_state(d, current.id))
    except social.OAuthError as exc:
        raise AppError(422, "not_configured", str(exc)) from exc
    db.commit()
    return {"authorize_url": url, "redirect_uri": social.redirect_uri()}


@router.get("/oauth/callback", include_in_schema=False)
def oauth_callback(
    state: str | None = None, code: str | None = None, error: str | None = None,
    error_description: str | None = None, error_reason: str | None = None, db: Session = Depends(get_db),
):
    """Facebook and Google send the browser here after sign-in. The signed, single-use state
    (not the session cookie) proves which destination and administrator started it."""
    try:
        d, user_id = social.read_state(db, state)
    except social.OAuthError as exc:
        return _settings_redirect(connect_error=str(exc))
    db.commit()  # the state is used up whatever happens next
    if error or not code:
        cancelled = error in ("access_denied",) or error_reason == "user_denied"
        msg = ("You cancelled the connection, so nothing was changed." if cancelled
               else f"Sign-in failed: {error_description or error or 'no authorisation code was returned'}")
        return _settings_redirect(connect_error=msg[:300], destination=str(d.id))
    actor = db.get(User, uuid.UUID(user_id)) if user_id else None
    try:
        with make_client(timeout=30) as client:
            message = social.complete_oauth(d, code, client)
    except social.OAuthError as exc:
        db.rollback()
        return _settings_redirect(connect_error=str(exc)[:300], destination=str(d.id))
    except Exception as exc:  # noqa: BLE001
        from app.core.http import describe_error

        db.rollback()
        return _settings_redirect(connect_error=f"Could not reach {d.kind.title()}: {describe_error(exc)}"[:300],
                                  destination=str(d.id))
    d.last_checked_at = utcnow()
    d.last_check_status = ("OK: " + message)[:300]
    record(db, "destination.connected", f"{d.name}: {message}", actor=actor, entity_type="destination", entity_id=d.id)
    db.commit()
    return _settings_redirect(connected=str(d.id))


@router.post("/destinations/{dest_id}/facebook-page", response_model=DestinationOut)
def choose_facebook_page(dest_id: uuid.UUID, body: FacebookPageIn, db: Session = Depends(get_db),
                         current: CurrentUser = Depends(require(P.PUBLISHING_MANAGE))):
    d = get_or_404(db, PublishingDestination, dest_id, "Destination")
    if d.kind != "facebook":
        raise AppError(400, "bad_request", "Only Facebook destinations have Pages")
    try:
        message = social.choose_page(d, body.page_id)
    except social.OAuthError as exc:
        raise AppError(409, "conflict", str(exc)) from exc
    d.last_checked_at = utcnow()
    d.last_check_status = ("OK: " + message)[:300]
    record(db, "destination.connected", f"{d.name}: {message}", actor=current, entity_type="destination", entity_id=d.id)
    db.commit()
    return dest_out(d)


@router.post("/destinations/{dest_id}/disconnect", response_model=DestinationOut)
def disconnect(dest_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.PUBLISHING_MANAGE))):
    d = get_or_404(db, PublishingDestination, dest_id, "Destination")
    if d.kind not in social.SOCIAL_KINDS:
        raise AppError(400, "bad_request", "Only Facebook and YouTube destinations can be disconnected")
    social.clear_connection(d)
    d.last_check_status = None
    d.last_checked_at = None
    record(db, "destination.disconnected", f"{d.name} disconnected", actor=current, entity_type="destination", entity_id=d.id)
    db.commit()
    return dest_out(d)


@router.get("/jobs")
def jobs(
    status: list[Literal["pending", "running", "succeeded", "failed", "cancelled"]] | None = Query(None),
    destination_id: uuid.UUID | None = None,
    article_id: uuid.UUID | None = None,
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require(P.READ)),
):
    stmt = select(PublishingJob)
    if status:
        stmt = stmt.where(PublishingJob.status.in_(status))
    if destination_id:
        stmt = stmt.where(PublishingJob.destination_id == destination_id)
    if article_id:
        stmt = stmt.where(PublishingJob.article_id == article_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(db.scalars(stmt.order_by(PublishingJob.created_at.desc()).offset(params.offset).limit(params.page_size)))
    heads = dict(db.execute(select(Article.id, Article.headline).where(Article.id.in_({j.article_id for j in rows}))).all()) if rows else {}
    items = [JobOut.model_validate(j).model_copy(update={"article_headline": heads.get(j.article_id),
                                                          "destination_name": j.destination.name,
                                                          "destination_kind": j.destination.kind}) for j in rows]
    counts = dict(db.execute(select(PublishingJob.status, func.count()).group_by(PublishingJob.status)).all())
    return {"items": items, "total": total, "page": params.page, "page_size": params.page_size,
            "status_counts": {k: int(v) for k, v in counts.items()}}


@router.post("/jobs/run")
def run_queue(db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.ARTICLES_PUBLISH))):
    stats = run_publishing_cycle(db)
    record(db, "publishing.queue_run", f"Publishing queue run manually: {stats}", actor=current, entity_type="publishing")
    db.commit()
    return stats


@router.post("/jobs/{job_id}/retry", response_model=JobOut)
def retry(job_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.ARTICLES_PUBLISH))):
    job = retry_job(db, get_or_404(db, PublishingJob, job_id, "Job"), current)
    db.commit()
    return job


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel(job_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.ARTICLES_PUBLISH))):
    job = cancel_job(db, get_or_404(db, PublishingJob, job_id, "Job"), current)
    db.commit()
    return job


@router.post("/publications/{pub_id}/sync", response_model=PublicationOut)
def sync(pub_id: uuid.UUID, db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.ARTICLES_PUBLISH))):
    p = sync_publication(db, get_or_404(db, ArticlePublication, pub_id, "Publication"))
    db.commit()
    return publication_out(p)
