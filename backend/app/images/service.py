"""Image library: save candidates, approve (download + thumbnail), reject, upload own images."""

from __future__ import annotations

import io
import json
import uuid
from datetime import datetime
from pathlib import Path

import httpx
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.core.http import describe_error, make_client, safe_request
from app.core.text import sha256_hex
from app.images.licenses import TERMS, evaluate_use, get_or_create_license
from app.images.providers import ImageCandidate, build_attribution
from app.models import ImageAsset

ALLOWED_FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif"}
THUMB_SIZE = (480, 480)
Image.MAX_IMAGE_PIXELS = 80_000_000  # decompression-bomb guard


class ImageError(Exception):
    pass


def save_candidate(db: Session, c: ImageCandidate, *, assignment_id=None, query: str | None = None) -> ImageAsset:
    lic = get_or_create_license(db, c.license_code, c.license_version, c.license_url)
    asset = db.scalar(select(ImageAsset).where(ImageAsset.provider == c.provider, ImageAsset.provider_id == c.provider_id))
    if asset is None:
        asset = ImageAsset(provider=c.provider, provider_id=c.provider_id)
        db.add(asset)
    asset.repository = c.repository
    asset.title = (c.title or "")[:500] or None
    asset.creator = (c.creator or "")[:500] or None
    asset.creator_url = c.creator_url
    asset.source_page_url = c.source_page_url
    asset.original_url = c.original_url
    asset.thumbnail_url = c.thumbnail_url
    asset.width, asset.height, asset.mime = c.width, c.height, c.mime
    asset.license_id = lic.id
    asset.license_raw = c.license_raw
    asset.license_url = c.license_url or lic.url
    asset.attribution_text = c.attribution_text or build_attribution(c.title, c.creator, lic.name, c.repository)
    asset.retrieved_at = utcnow()
    asset.provider_metadata = c.metadata
    if assignment_id is not None:
        asset.assignment_id = assignment_id
    if query:
        asset.search_query = query[:300]
    db.flush()
    return asset


def warnings_for(asset: ImageAsset, *, commercial: bool = True, modified: bool = False) -> list[dict]:
    lic = asset.license
    return [
        w.as_dict()
        for w in evaluate_use(
            license_code=lic.code if lic else None,
            allows_commercial=lic.allows_commercial if lic else None,
            allows_derivatives=lic.allows_derivatives if lic else None,
            requires_attribution=lic.requires_attribution if lic else None,
            share_alike=lic.share_alike if lic else False,
            attribution_text=asset.attribution_text,
            approval_status=asset.approval_status,
            commercial_publication=commercial,
            modifications_planned=modified,
            downloaded=asset.download_status == "downloaded",
        )
    ]


def _store_bytes(content: bytes, asset: ImageAsset) -> None:
    settings = get_settings()
    try:
        probe = Image.open(io.BytesIO(content))
        probe.verify()
        img = Image.open(io.BytesIO(content))
        fmt = img.format
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ImageError(f"Not a valid image: {exc}") from exc
    if fmt not in ALLOWED_FORMATS:
        raise ImageError(f"Unsupported image format: {fmt}")
    ext = ALLOWED_FORMATS[fmt]
    now = datetime.now()
    rel_dir = Path(f"{now:%Y}/{now:%m}")
    out_dir = settings.images_dir / rel_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = uuid.uuid4().hex
    original = out_dir / f"{stem}.{ext}"
    # The original file is stored byte-for-byte: embedded licence/creator metadata is preserved.
    original.write_bytes(content)
    thumb = out_dir / f"{stem}_thumb.jpg"
    t = img.convert("RGB") if img.mode not in ("RGB", "L") else img.copy()
    t.thumbnail(THUMB_SIZE)
    t.save(thumb, "JPEG", quality=82, optimize=True)
    asset.local_path = str(rel_dir / original.name)
    asset.thumbnail_path = str(rel_dir / thumb.name)
    asset.file_sha256 = sha256_hex(content)
    asset.file_size = len(content)
    asset.width, asset.height = img.size
    asset.mime = Image.MIME.get(fmt)
    # Sidecar keeps licence and attribution next to the file (survives restores and copies).
    sidecar = out_dir / f"{stem}.license.json"
    lic = asset.license
    sidecar.write_text(json.dumps({
        "provider": asset.provider, "provider_id": asset.provider_id, "title": asset.title,
        "creator": asset.creator, "source_page_url": asset.source_page_url, "original_url": asset.original_url,
        "license": lic.name if lic else None, "license_code": lic.code if lic else None,
        "license_url": asset.license_url, "attribution": asset.attribution_text,
        "retrieved_at": asset.retrieved_at.isoformat() if asset.retrieved_at else None,
        "sha256": asset.file_sha256,
    }, indent=2))


def download(db: Session, asset: ImageAsset, client: httpx.Client | None = None) -> None:
    if not asset.original_url:
        raise ImageError("Image has no original URL")
    own = client is None
    client = client or make_client(timeout=30)
    try:
        resp = safe_request(client, "GET", asset.original_url, max_bytes=get_settings().max_image_bytes,
                            headers={"Accept": "image/*"})
        if resp.status_code != 200:
            raise ImageError(f"Download failed: HTTP {resp.status_code}")
        ctype = resp.headers.get("content-type", "")
        if not ctype.startswith("image/"):
            raise ImageError(f"Download did not return an image (content-type {ctype or 'missing'})")
        _store_bytes(resp.content, asset)
        asset.download_status = "downloaded"
        asset.download_error = None
    except ImageError as exc:
        asset.download_status = "failed"
        asset.download_error = str(exc)
        raise
    except Exception as exc:
        asset.download_status = "failed"
        asset.download_error = describe_error(exc)
        raise ImageError(asset.download_error) from exc
    finally:
        if own:
            client.close()
        db.flush()


def approve(db: Session, asset: ImageAsset, user_id, *, client: httpx.Client | None = None) -> list[dict]:
    """Approve after licence checks; downloads the file. Unknown licences cannot be approved."""
    lic = asset.license
    if lic is None or not lic.is_known:
        raise ImageError("Images with an unknown licence cannot be approved")
    if lic.requires_attribution and not (asset.attribution_text or "").strip():
        raise ImageError("Attribution text is required before approval")
    if asset.provider != "upload" and asset.download_status != "downloaded":
        download(db, asset, client)
    asset.approval_status = "approved"
    asset.approved_by_id = user_id
    asset.approved_at = utcnow()
    db.flush()
    return warnings_for(asset)


def reject(db: Session, asset: ImageAsset, note: str | None = None) -> None:
    asset.approval_status = "rejected"
    if note:
        asset.notes = note
    db.flush()


def upload(
    db: Session, content: bytes, filename: str, *, title: str | None, creator: str | None,
    license_code: str, attribution_text: str | None, source_page_url: str | None, user_id,
) -> ImageAsset:
    if len(content) > get_settings().max_upload_bytes:
        raise ImageError("File is too large")
    if license_code not in TERMS or license_code == "unknown":
        raise ImageError("Choose the licence under which you may use this image")
    lic = get_or_create_license(db, license_code, "4.0" if license_code.startswith("by") else "")
    asset = ImageAsset(
        provider="upload", provider_id=uuid.uuid4().hex, repository="Upload", title=(title or filename)[:500],
        creator=creator, source_page_url=source_page_url, license_id=lic.id, license_url=lic.url,
        attribution_text=attribution_text or (build_attribution(title, creator, lic.name, None) if lic.requires_attribution else None),
    )
    asset.license = lic
    _store_bytes(content, asset)
    asset.download_status = "downloaded"
    db.add(asset)
    db.flush()
    return asset
