"""Licence normalisation and the publication warning system.

An image is never treated as free to use just because a search returned it. Its licence is
mapped to a known code; anything that cannot be mapped is `unknown` and blocked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ImageLicense


@dataclass(frozen=True)
class LicenseTerms:
    code: str
    name: str
    commercial: bool | None
    derivatives: bool | None
    attribution: bool | None
    share_alike: bool
    known: bool = True


TERMS: dict[str, LicenseTerms] = {
    "cc0": LicenseTerms("cc0", "CC0 Public Domain Dedication", True, True, False, False),
    "pdm": LicenseTerms("pdm", "Public Domain Mark", True, True, False, False),
    "by": LicenseTerms("by", "CC BY", True, True, True, False),
    "by-sa": LicenseTerms("by-sa", "CC BY-SA", True, True, True, True),
    "by-nc": LicenseTerms("by-nc", "CC BY-NC", False, True, True, False),
    "by-nd": LicenseTerms("by-nd", "CC BY-ND", True, False, True, False),
    "by-nc-sa": LicenseTerms("by-nc-sa", "CC BY-NC-SA", False, True, True, True),
    "by-nc-nd": LicenseTerms("by-nc-nd", "CC BY-NC-ND", False, False, True, False),
    "own": LicenseTerms("own", "Own work / rights held by the publication", True, True, False, False),
    "other": LicenseTerms("other", "Other licence (review terms manually)", None, None, None, False, known=False),
    "unknown": LicenseTerms("unknown", "Unknown licence", None, None, None, False, known=False),
}

_CC_URL = re.compile(r"creativecommons\.org/(licenses|publicdomain)/([a-z\-]+)/(\d(?:\.\d)?)?", re.I)


def normalize_license(raw: str | None, version: str | None = None, url: str | None = None) -> tuple[str, str]:
    """Map provider licence strings/URLs to (code, version)."""
    version = (version or "").strip()
    if url:
        m = _CC_URL.search(url)
        if m:
            kind, slug, ver = m.group(1).lower(), m.group(2).lower(), m.group(3) or version
            if kind == "publicdomain":
                return ("cc0" if slug == "zero" else "pdm"), (ver or "")
            if slug in TERMS:
                return slug, ver or ""
    s = (raw or "").strip().lower().replace("_", "-")
    if not s:
        return "unknown", ""
    s = s.replace("creative commons", "cc").replace("attribution", "by").replace("sharealike", "sa")
    m = re.search(r"(\d\.\d|\d)", s.replace("cc0", ""))  # the 0 in "CC0" is not a version
    ver = version or (m.group(1) if m else "")
    if s in ("cc0", "cc-zero", "cc0-1.0") or "cc0" in s or "public domain dedication" in s:
        return "cc0", ver or "1.0"
    if s in ("pdm", "public domain", "pd", "public-domain") or s.startswith("pd-") or "public domain mark" in s:
        return "pdm", ""
    core = re.sub(r"^cc[\s\-]*", "", s)
    core = re.sub(r"[\s\-]*\d(\.\d)?.*$", "", core).strip(" -")
    core = core.replace(" ", "-")
    parts = [p for p in core.split("-") if p in ("by", "sa", "nc", "nd")]
    if parts and parts[0] == "by":
        order = ["by"] + [p for p in ("nc", "nd", "sa") if p in parts[1:]]
        if "nd" in order and "sa" in order:
            return "other", ver
        code = "-".join(order)
        if code == "by-nd-nc":
            code = "by-nc-nd"
        if code in TERMS:
            return code, ver
    if "gfdl" in s or "gnu free documentation" in s:
        return "other", ver
    return "unknown", ""


def license_url_for(code: str, version: str) -> str | None:
    if code == "cc0":
        return "https://creativecommons.org/publicdomain/zero/1.0/"
    if code == "pdm":
        return "https://creativecommons.org/publicdomain/mark/1.0/"
    if code in TERMS and code.startswith("by"):
        return f"https://creativecommons.org/licenses/{code}/{version or '4.0'}/"
    return None


def get_or_create_license(db: Session, code: str, version: str = "", url: str | None = None) -> ImageLicense:
    terms = TERMS.get(code, TERMS["unknown"])
    row = db.scalar(select(ImageLicense).where(ImageLicense.code == terms.code, ImageLicense.version == version))
    if row is not None:
        return row
    name = terms.name + (f" {version}" if version and terms.code.startswith("by") else "")
    row = ImageLicense(
        code=terms.code,
        version=version,
        name=name,
        url=url or license_url_for(terms.code, version),
        allows_commercial=terms.commercial,
        allows_derivatives=terms.derivatives,
        requires_attribution=terms.attribution,
        share_alike=terms.share_alike,
        is_known=terms.known,
    )
    db.add(row)
    db.flush()
    return row


@dataclass
class LicenseWarning:
    level: str  # error | warning | info
    code: str
    message: str

    def as_dict(self) -> dict:
        return {"level": self.level, "code": self.code, "message": self.message}


def evaluate_use(
    *,
    license_code: str | None,
    allows_commercial: bool | None,
    allows_derivatives: bool | None,
    requires_attribution: bool | None,
    share_alike: bool,
    attribution_text: str | None,
    approval_status: str | None = None,
    commercial_publication: bool = True,
    modifications_planned: bool = False,
    downloaded: bool | None = None,
) -> list[LicenseWarning]:
    w: list[LicenseWarning] = []
    code = license_code or "unknown"
    if code in ("unknown", "other"):
        w.append(LicenseWarning("error", "unknown_license",
                                "Rights for this image could not be established; it cannot be published automatically."))
    if commercial_publication and allows_commercial is False:
        w.append(LicenseWarning("error", "non_commercial",
                                "NonCommercial licence: not allowed on a commercial publication."))
    if commercial_publication and allows_commercial is None and code not in ("unknown", "other"):
        w.append(LicenseWarning("warning", "commercial_undocumented", "Commercial use is not documented."))
    if modifications_planned and allows_derivatives is False:
        w.append(LicenseWarning("error", "no_derivatives",
                                "NoDerivatives licence: the image may not be cropped or altered."))
    if requires_attribution and not (attribution_text or "").strip():
        w.append(LicenseWarning("error", "attribution_missing", "This licence requires attribution, but no attribution text is recorded."))
    if share_alike:
        w.append(LicenseWarning("warning", "share_alike",
                                "ShareAlike: adaptations of this image must be shared under the same licence."))
    if requires_attribution:
        w.append(LicenseWarning("info", "attribution_required", "Attribution must be shown wherever the image is used."))
    if approval_status is not None and approval_status != "approved":
        w.append(LicenseWarning("error", "not_approved", "An editor has not approved this image yet."))
    if downloaded is False:
        w.append(LicenseWarning("warning", "not_downloaded", "Image is not stored locally yet; it will be downloaded on approval."))
    return w


def has_blocking(warnings: list[LicenseWarning]) -> bool:
    return any(x.level == "error" for x in warnings)
