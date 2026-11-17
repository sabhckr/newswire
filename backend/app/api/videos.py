"""Video uploads. The file is the raw request body (not multipart), streamed straight to disk."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect

from app.api.common import get_or_404
from app.core.db import get_db
from app.core.errors import AppError
from app.models import VideoAsset
from app.schemas.domain import VideoOut, VideoUploadOut, VideoUploadStartIn
from app.security import permissions as P
from app.security.deps import CurrentUser, require
from app.services.audit import record
from app.videos import service

router = APIRouter(prefix="/videos", tags=["videos"])


def _error(exc: service.VideoError) -> AppError:
    code = {413: "payload_too_large", 404: "not_found", 409: "conflict", 507: "insufficient_storage"}.get(
        exc.status, "invalid_video")
    return AppError(exc.status, code, str(exc), exc.details)


def _interrupted() -> AppError:
    # The browser is gone, so nobody reads this; it keeps the log free of tracebacks.
    return AppError(400, "upload_interrupted", "The upload was interrupted. Try again.")


def video_out(v: VideoAsset) -> VideoOut:
    o = VideoOut.model_validate(v)
    o.url = f"/media/{v.local_path}"
    o.reel_problems = service.reel_problems(v)
    o.is_short = service.is_short(v)
    return o


@router.post("", response_model=VideoOut, status_code=201)
async def upload(
    request: Request,
    filename: str | None = Query(None, max_length=255),
    title: str | None = Query(None, max_length=500),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(require(P.ARTICLES_WRITE)),
):
    """Send the whole video file as the request body (Content-Type video/mp4 or video/quicktime).

    For scripts talking to the API directly. Browsers use /videos/uploads, which sends the file in
    pieces: a single large request fails behind proxies with body limits (Next.js cuts proxied
    bodies at 10 MB)."""
    declared = request.headers.get("content-length")
    try:
        asset = await service.store_upload(
            db, request.stream(), filename=filename, title=title,
            declared_size=int(declared) if declared and declared.isdigit() else None, user_id=current.id,
        )
    except service.VideoError as exc:
        raise _error(exc) from exc
    except ClientDisconnect:
        raise _interrupted() from None
    return _uploaded(db, asset, current)


def _uploaded(db: Session, asset: VideoAsset, current: CurrentUser) -> VideoOut:
    record(db, "video.uploaded", f"Video “{(asset.title or asset.original_filename or '')[:80]}” uploaded "
           f"({asset.file_size / 1_000_000:.1f} MB)", actor=current, entity_type="video", entity_id=asset.id)
    db.commit()
    return video_out(asset)


@router.post("/uploads", response_model=VideoUploadOut, status_code=201)
def start_upload(body: VideoUploadStartIn, current: CurrentUser = Depends(require(P.ARTICLES_WRITE))):
    """Begin an upload in pieces. Then PUT each piece (at most chunk_size bytes) to
    /videos/uploads/{id}?offset=N, and POST /videos/uploads/{id}/complete."""
    try:
        return service.start_upload(filename=body.filename, title=body.title, size=body.size, user_id=current.id)
    except service.VideoError as exc:
        raise _error(exc) from exc


@router.get("/uploads/{upload_id}", response_model=VideoUploadOut)
def upload_status(upload_id: str, current: CurrentUser = Depends(require(P.ARTICLES_WRITE))):
    """How much has arrived, to resume after a dropped connection."""
    try:
        return service.get_upload(upload_id, current.id)
    except service.VideoError as exc:
        raise _error(exc) from exc


@router.put("/uploads/{upload_id}", response_model=VideoUploadOut)
async def upload_piece(
    upload_id: str, request: Request, offset: int = Query(..., ge=0),
    current: CurrentUser = Depends(require(P.ARTICLES_WRITE)),
):
    """One piece of the file as the raw body. A wrong offset answers 409 with details.received."""
    try:
        return await service.append_chunk(upload_id, current.id, offset, request.stream())
    except service.VideoError as exc:
        raise _error(exc) from exc
    except ClientDisconnect:
        raise _interrupted() from None


@router.post("/uploads/{upload_id}/complete", response_model=VideoOut, status_code=201)
def complete_upload(upload_id: str, db: Session = Depends(get_db),
                    current: CurrentUser = Depends(require(P.ARTICLES_WRITE))):
    try:
        asset = service.finish_upload(db, upload_id, current.id)
    except service.VideoError as exc:
        raise _error(exc) from exc
    return _uploaded(db, asset, current)


@router.delete("/uploads/{upload_id}")
def cancel_upload(upload_id: str, current: CurrentUser = Depends(require(P.ARTICLES_WRITE))):
    service.cancel_upload(upload_id, current.id)
    return {"ok": True}


@router.get("/{video_id}", response_model=VideoOut)
def get_video(video_id: uuid.UUID, db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.READ))):
    return video_out(get_or_404(db, VideoAsset, video_id, "Video"))


@router.delete("/{video_id}")
def delete_video(video_id: uuid.UUID, db: Session = Depends(get_db),
                 current: CurrentUser = Depends(require(P.ARTICLES_WRITE))):
    v = get_or_404(db, VideoAsset, video_id, "Video")
    if service.in_use(db, v):
        raise AppError(409, "in_use", "This video is used in an article; remove it there first")
    record(db, "video.deleted", f"Video “{(v.title or '')[:80]}” deleted", actor=current,
           entity_type="video", entity_id=v.id)
    service.delete_video(db, v)
    db.commit()
    return {"ok": True}
