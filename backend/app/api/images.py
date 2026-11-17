from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.common import get_or_404
from app.api.research import image_out
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError
from app.core.pagination import PageParams
from app.images import service
from app.images.licenses import evaluate_use, has_blocking, TERMS
from app.images.providers import ImageCandidate, ProviderError, search_openverse, search_wikimedia
from app.models import Article, ArticleImage, EditorialAssignment, ImageAsset, ImageLicense
from app.schemas.domain import ImageOut, ImageRejectIn, ImageSaveIn, ImageUpdateIn, LicenseOut
from app.security import permissions as P
from app.security.deps import CurrentUser, require
from app.services.audit import record

router = APIRouter(prefix="/images", tags=["images"])


@router.get("/search")
def search(
    q: str = Query(min_length=2, max_length=200),
    provider: Literal["openverse", "wikimedia"] = "openverse",
    page: int = Query(1, ge=1, le=50),
    commercial_only: bool = False,
    modification_allowed: bool = False,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require(P.IMAGES_WRITE)),
):
    """Search an open repository. Results are not stored until an editor saves one."""
    try:
        if provider == "openverse":
            res = search_openverse(q, page=page, commercial_only=commercial_only, modification_allowed=modification_allowed)
        else:
            res = search_wikimedia(q, page=page)
    except ProviderError as exc:
        raise AppError(502, "provider_error", str(exc)) from exc
    saved = {
        (p, pid): (sid, status)
        for p, pid, sid, status in db.execute(
            select(ImageAsset.provider, ImageAsset.provider_id, ImageAsset.id, ImageAsset.approval_status).where(
                ImageAsset.provider == provider, ImageAsset.provider_id.in_([c.provider_id for c in res["results"]])))
    }
    out = []
    for c in res["results"]:
        terms = TERMS.get(c.license_code, TERMS["unknown"])
        warnings = evaluate_use(
            license_code=c.license_code, allows_commercial=terms.commercial, allows_derivatives=terms.derivatives,
            requires_attribution=terms.attribution, share_alike=terms.share_alike, attribution_text=c.attribution_text,
        )
        d = c.as_dict()
        d["license_name"] = terms.name + (f" {c.license_version}" if c.license_version and c.license_code.startswith("by") else "")
        d["warnings"] = [w.as_dict() for w in warnings]
        d["blocked"] = has_blocking(warnings)
        hit = saved.get((c.provider, c.provider_id))
        d["saved_id"] = str(hit[0]) if hit else None
        d["saved_status"] = hit[1] if hit else None
        out.append(d)
    return {**res, "results": out}


@router.get("/licenses", response_model=list[LicenseOut])
def licenses(db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.READ))):
    return list(db.scalars(select(ImageLicense).order_by(ImageLicense.code, ImageLicense.version)))


@router.get("")
def library(
    status: Literal["pending", "approved", "rejected"] | None = None,
    provider: str | None = None,
    assignment_id: uuid.UUID | None = None,
    license_code: str | None = None,
    q: str | None = None,
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require(P.READ)),
):
    stmt = select(ImageAsset)
    if status:
        stmt = stmt.where(ImageAsset.approval_status == status)
    if provider:
        stmt = stmt.where(ImageAsset.provider == provider)
    if assignment_id:
        stmt = stmt.where(ImageAsset.assignment_id == assignment_id)
    if license_code:
        stmt = stmt.where(ImageAsset.license_id.in_(select(ImageLicense.id).where(ImageLicense.code == license_code)))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(ImageAsset.title.ilike(like), ImageAsset.creator.ilike(like), ImageAsset.search_query.ilike(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = list(db.scalars(stmt.order_by(ImageAsset.created_at.desc()).offset(params.offset).limit(params.page_size)))
    return {"items": [image_out(i) for i in items], "total": total, "page": params.page, "page_size": params.page_size}


@router.post("", response_model=ImageOut, status_code=201)
def save(body: ImageSaveIn, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.IMAGES_WRITE))):
    if body.assignment_id:
        get_or_404(db, EditorialAssignment, body.assignment_id, "Assignment")
    asset = service.save_candidate(db, ImageCandidate(**body.candidate.model_dump()), assignment_id=body.assignment_id, query=body.query)
    record(db, "image.saved", f"Image “{(asset.title or '')[:80]}” saved from {asset.provider} for review",
           actor=current, entity_type="image", entity_id=asset.id)
    db.commit()
    return image_out(asset)


@router.post("/upload", response_model=ImageOut, status_code=201)
async def upload(
    file: UploadFile = File(...),
    license_code: str = Form(...),
    title: str | None = Form(None),
    creator: str | None = Form(None),
    attribution_text: str | None = Form(None),
    source_page_url: str | None = Form(None),
    assignment_id: uuid.UUID | None = Form(None),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(require(P.IMAGES_WRITE)),
):
    limit = get_settings().max_upload_bytes
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise AppError(413, "payload_too_large", f"Images must be smaller than {limit // 1_000_000} MB")
    try:
        asset = service.upload(db, content, file.filename or "upload", title=title, creator=creator,
                               license_code=license_code, attribution_text=attribution_text,
                               source_page_url=source_page_url, user_id=current.id)
    except service.ImageError as exc:
        raise AppError(422, "invalid_image", str(exc)) from exc
    asset.assignment_id = assignment_id
    record(db, "image.uploaded", f"Image “{(asset.title or '')[:80]}” uploaded ({license_code})", actor=current,
           entity_type="image", entity_id=asset.id)
    db.commit()
    return image_out(asset)


@router.get("/{image_id}", response_model=ImageOut)
def get_image(image_id: uuid.UUID, commercial: bool = True, db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.READ))):
    return image_out(get_or_404(db, ImageAsset, image_id, "Image"), commercial=commercial)


@router.patch("/{image_id}", response_model=ImageOut)
def update_image(image_id: uuid.UUID, body: ImageUpdateIn, db: Session = Depends(get_db),
                 current: CurrentUser = Depends(require(P.IMAGES_WRITE))):
    asset = get_or_404(db, ImageAsset, image_id, "Image")
    data = body.model_dump(exclude_unset=True)
    if "attribution_text" in data and asset.approval_status == "approved" and not current.can(P.IMAGES_APPROVE):
        raise AppError(403, "forbidden", "Only an approver can change the attribution of an approved image")
    for k, v in data.items():
        setattr(asset, k, v)
    record(db, "image.updated", f"Image “{(asset.title or '')[:80]}” metadata updated", actor=current,
           entity_type="image", entity_id=asset.id, details={"fields": list(data)})
    db.commit()
    return image_out(asset)


@router.post("/{image_id}/approve", response_model=ImageOut)
def approve(image_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.IMAGES_APPROVE))):
    asset = get_or_404(db, ImageAsset, image_id, "Image")
    try:
        service.approve(db, asset, current.id)
    except service.ImageError as exc:
        db.commit()  # keep the recorded download failure
        raise AppError(422, "image_not_approvable", str(exc)) from exc
    record(db, "image.approved", f"Image “{(asset.title or '')[:80]}” approved ({asset.license.name if asset.license else 'unknown'})",
           actor=current, entity_type="image", entity_id=asset.id)
    db.commit()
    return image_out(asset)


@router.post("/{image_id}/reject", response_model=ImageOut)
def reject(image_id: uuid.UUID, body: ImageRejectIn, db: Session = Depends(get_db),
           current: CurrentUser = Depends(require(P.IMAGES_APPROVE))):
    asset = get_or_404(db, ImageAsset, image_id, "Image")
    service.reject(db, asset, body.note)
    record(db, "image.rejected", f"Image “{(asset.title or '')[:80]}” rejected", actor=current, entity_type="image", entity_id=asset.id)
    db.commit()
    return image_out(asset)


@router.delete("/{image_id}")
def delete_image(image_id: uuid.UUID, db: Session = Depends(get_db), current: CurrentUser = Depends(require(P.IMAGES_APPROVE))):
    asset = get_or_404(db, ImageAsset, image_id, "Image")
    used = db.scalar(select(func.count()).select_from(ArticleImage).where(ArticleImage.image_asset_id == asset.id)) or 0
    used += db.scalar(select(func.count()).select_from(Article).where(Article.featured_image_id == asset.id)) or 0
    if used:
        raise AppError(409, "in_use", "This image is used in an article; remove it there first")
    settings = get_settings()
    for rel in (asset.local_path, asset.thumbnail_path):
        if rel:
            p = settings.images_dir / rel
            if p.exists():
                p.unlink()
    if asset.local_path:
        sidecar = settings.images_dir / (asset.local_path.rsplit(".", 1)[0] + ".license.json")
        if sidecar.exists():
            sidecar.unlink()
    record(db, "image.deleted", f"Image “{(asset.title or '')[:80]}” deleted", actor=current, entity_type="image", entity_id=asset.id)
    db.delete(asset)
    db.commit()
    return {"ok": True}
