"""Video library: store an uploaded MP4/MOV, describe it, check it against platform rules."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.models import Article, VideoAsset
from app.videos.mp4 import NotAVideo, probe

# Facebook Reels: 3 to 90 seconds, vertical (9:16 recommended, at least 540 x 960).
REEL_MIN_SECONDS = 3
REEL_MAX_SECONDS = 90
# YouTube treats vertical videos up to this length as Shorts.
SHORTS_MAX_SECONDS = 180
# Browsers send videos in pieces of this size. It stays under every proxy's default body limit
# (Next.js 10 MB, nginx/Caddy as configured here 20 MB, Cloudflare 100 MB) and lets a dropped
# connection resume instead of starting over.
UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024
# Unfinished uploads are deleted after this long.
UPLOAD_MAX_AGE_SECONDS = 24 * 3600


class VideoError(Exception):
    def __init__(self, message: str, status: int = 422, details: dict | None = None):
        super().__init__(message)
        self.status = status
        self.details = details


def uploads_dir() -> Path:
    """Unfinished uploads live outside /media, so they are never served or backed up."""
    d = get_settings().storage_dir / "uploads" / "videos"
    d.mkdir(parents=True, exist_ok=True)
    return d


def video_path(video: VideoAsset) -> Path:
    return get_settings().images_dir / video.local_path


def video_url(video: VideoAsset) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/media/{video.local_path}"


def require_file(video: VideoAsset) -> Path:
    path = video_path(video)
    if not path.is_file():
        raise VideoError("The video file is missing on the Samyabadi server (it may not have been restored from a "
                         "backup). Upload the video again.", 409)
    return path


async def store_upload(
    db: Session, chunks: AsyncIterator[bytes], *, filename: str | None, title: str | None,
    declared_size: int | None, user_id=None,
) -> VideoAsset:
    """Stream an upload to disk, then check that it is a readable MP4/MOV."""
    limit = get_settings().max_video_bytes
    if declared_size is not None and declared_size > limit:
        raise VideoError(_too_large(limit), 413)
    tmp = uploads_dir() / f"{uuid.uuid4().hex}.direct.part"
    digest = hashlib.sha256()
    size = 0
    try:
        with open(tmp, "wb") as out:
            async for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                if size > limit:
                    raise VideoError(_too_large(limit), 413)
                digest.update(chunk)
                out.write(chunk)
        return _register(db, tmp, size, digest.hexdigest(), filename=filename, title=title, user_id=user_id)
    finally:
        tmp.unlink(missing_ok=True)


def _too_large(limit: int) -> str:
    return f"The video is larger than {limit // 1_000_000} MB, the most this server accepts."


def _register(db: Session, tmp: Path, size: int, sha256: str, *, filename: str | None, title: str | None,
              user_id) -> VideoAsset:
    """Check a fully received file, move it into the media library and record it."""
    if size == 0:
        raise VideoError("The uploaded file is empty.")
    with open(tmp, "rb") as f:
        try:
            info = probe(f, size)
        except NotAVideo as exc:
            raise VideoError(str(exc)) from exc
    now = utcnow()
    rel_dir = f"videos/{now:%Y}/{now:%m}"
    out_dir = get_settings().images_dir / rel_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / f"{uuid.uuid4().hex}.{info.extension}"
    shutil.move(tmp, final)
    name = (filename or "").strip()[:255] or None
    asset = VideoAsset(
        title=(title or "").strip()[:500] or (Path(name).stem[:500] if name else None),
        original_filename=name,
        local_path=f"{rel_dir}/{final.name}",
        mime=info.mime,
        file_size=size,
        file_sha256=sha256,
        width=info.width,
        height=info.height,
        duration_seconds=info.duration_seconds,
        uploaded_by_id=user_id,
    )
    db.add(asset)
    try:
        db.flush()
    except BaseException:
        final.unlink(missing_ok=True)
        raise
    return asset


# --- Uploads in pieces (what the browser uses) ----------------------------------------------------

_UPLOAD_ID = re.compile(r"[0-9a-f]{32}")


def _upload_files(upload_id: str, user_id) -> tuple[Path, Path, dict]:
    gone = VideoError("This upload has expired or was cancelled. Choose the file again.", 404)
    if not _UPLOAD_ID.fullmatch(upload_id or ""):
        raise gone
    d = uploads_dir()
    part, meta_path = d / f"{upload_id}.part", d / f"{upload_id}.json"
    try:
        meta = json.loads(meta_path.read_text())
    except (OSError, ValueError):
        raise gone from None
    if meta.get("user_id") != str(user_id) or not part.is_file():
        raise gone
    return part, meta_path, meta


def upload_state(upload_id: str, meta: dict, received: int) -> dict:
    return {"upload_id": upload_id, "size": meta["size"], "received": received, "chunk_size": UPLOAD_CHUNK_BYTES}


def start_upload(*, filename: str | None, title: str | None, size: int, user_id) -> dict:
    limit = get_settings().max_video_bytes
    if size <= 0:
        raise VideoError("The file is empty.")
    if size > limit:
        raise VideoError(_too_large(limit), 413)
    free = shutil.disk_usage(uploads_dir()).free
    if free < size + 200_000_000:  # the file plus headroom; the final move is a rename on the same disk
        raise VideoError("The Samyabadi server is low on disk space, so the video cannot be stored. "
                         "Ask the administrator to free space.", 507)
    upload_id = uuid.uuid4().hex
    d = uploads_dir()
    meta = {"filename": (filename or "").strip()[:255] or None, "title": (title or "").strip()[:500] or None,
            "size": size, "user_id": str(user_id), "started": time.time()}
    (d / f"{upload_id}.part").touch()
    (d / f"{upload_id}.json").write_text(json.dumps(meta))
    return upload_state(upload_id, meta, 0)


def get_upload(upload_id: str, user_id) -> dict:
    part, _, meta = _upload_files(upload_id, user_id)
    return upload_state(upload_id, meta, part.stat().st_size)


async def append_chunk(upload_id: str, user_id, offset: int, chunks: AsyncIterator[bytes]) -> dict:
    """Write one piece at `offset`. A piece cut off by a dropped connection keeps what arrived
    (it is a correct prefix), and the browser continues from the size reported back."""
    part, _, meta = _upload_files(upload_id, user_id)
    received = part.stat().st_size
    if offset != received:
        raise VideoError("The upload is out of step; continuing from what the server has.", 409,
                         {"received": received})
    total = meta["size"]
    written = 0
    with open(part, "r+b") as f:
        f.seek(offset)
        try:
            async for chunk in chunks:
                if not chunk:
                    continue
                written += len(chunk)
                if written > UPLOAD_CHUNK_BYTES or offset + written > total:
                    raise VideoError("The upload sent more data than expected. Choose the file again.", 422)
                f.write(chunk)
        except VideoError:
            f.truncate(offset)
            raise
    return upload_state(upload_id, meta, offset + written)


def finish_upload(db: Session, upload_id: str, user_id) -> VideoAsset:
    part, meta_path, meta = _upload_files(upload_id, user_id)
    size = part.stat().st_size
    if size != meta["size"]:
        raise VideoError(f"The upload is incomplete ({size:,} of {meta['size']:,} bytes received).", 409,
                         {"received": size})
    digest = hashlib.sha256()
    with open(part, "rb") as f:
        while block := f.read(1 << 20):
            digest.update(block)
    try:
        return _register(db, part, size, digest.hexdigest(), filename=meta.get("filename"),
                         title=meta.get("title"), user_id=user_id)
    except VideoError:
        part.unlink(missing_ok=True)  # not a usable video: there is nothing to resume
        raise
    finally:
        meta_path.unlink(missing_ok=True)


def cancel_upload(upload_id: str, user_id) -> None:
    try:
        part, meta_path, _ = _upload_files(upload_id, user_id)
    except VideoError:
        return
    part.unlink(missing_ok=True)
    meta_path.unlink(missing_ok=True)


def prune_uploads(max_age_seconds: int = UPLOAD_MAX_AGE_SECONDS) -> int:
    """Delete unfinished uploads nobody has touched for a day."""
    cutoff = time.time() - max_age_seconds
    d = uploads_dir()

    def mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    removed = 0
    for meta_path in d.glob("*.json"):
        part = meta_path.with_suffix(".part")
        if max(mtime(meta_path), mtime(part)) < cutoff:  # judged by the last piece received
            part.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
            removed += 1
    for part in d.glob("*.part"):  # orphans, e.g. left by a crash mid-upload
        if not part.with_suffix(".json").exists() and mtime(part) < cutoff:
            part.unlink(missing_ok=True)
            removed += 1
    return removed


def in_use(db: Session, video: VideoAsset) -> int:
    return db.scalar(select(func.count()).select_from(Article).where(
        Article.video_id == video.id, Article.deleted_at.is_(None))) or 0


def delete_video(db: Session, video: VideoAsset) -> None:
    video_path(video).unlink(missing_ok=True)
    db.delete(video)
    db.flush()


def _fmt_seconds(s: float) -> str:
    s = round(s)
    return f"{s // 60}:{s % 60:02d}" if s >= 60 else f"{s} s"


def reel_problems(video: VideoAsset | None) -> list[str]:
    """Why this video cannot be a Facebook Reel (empty when it can, or when the file does not say)."""
    if video is None:
        return ["A Reel needs a video. Upload one in the article's Video box."]
    out = []
    d = video.duration_seconds
    if d is not None and d < REEL_MIN_SECONDS:
        out.append(f"Reels must be at least {REEL_MIN_SECONDS} seconds long; this video is {_fmt_seconds(d)}.")
    if d is not None and d > REEL_MAX_SECONDS:
        out.append(f"Reels can be at most {REEL_MAX_SECONDS} seconds long; this video is {_fmt_seconds(d)}. "
                   "Post it as a Facebook video post or on YouTube instead, or trim it.")
    if video.width and video.height and video.width >= video.height:
        out.append(f"Reels must be vertical (9:16); this video is {video.width}×{video.height}. "
                   "Use a vertical video, or choose Post instead of Reel.")
    return out


def is_short(video: VideoAsset) -> bool:
    return bool(video.width and video.height and video.height > video.width
                and video.duration_seconds is not None and video.duration_seconds <= SHORTS_MAX_SECONDS)


def file_chunks(path: Path, chunk_size: int = 1 << 20):
    """A re-iterable body for httpx, so large uploads are streamed instead of read into memory."""

    class _Body:
        def __iter__(self):
            with open(path, "rb") as f:
                while chunk := f.read(chunk_size):
                    yield chunk

    return _Body()


def file_range(path: Path, start: int, chunk_size: int = 1 << 20):
    class _Body:
        def __iter__(self):
            with open(path, "rb") as f:
                f.seek(start)
                while chunk := f.read(chunk_size):
                    yield chunk

    return _Body()
