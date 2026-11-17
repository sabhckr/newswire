"""Sanitising, rendering and the optional factual template."""

from __future__ import annotations

import html
import re

import nh3
from bs4 import BeautifulSoup

from app.core.config import get_settings
from app.images.licenses import evaluate_use
from app.models import Article, EditorialAssignment, ImageAsset

ALLOWED_TAGS = {
    "p", "br", "hr", "h2", "h3", "h4", "strong", "b", "em", "i", "u", "s", "code", "pre",
    "blockquote", "ul", "ol", "li", "a", "figure", "figcaption", "img", "span",
}
ALLOWED_ATTRS = {
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "title", "data-image-id", "width", "height"},
    "span": {"data-attribution"},
    "figure": {"data-image-id"},
}

TEMPLATE_MARKER = "[EDITOR:"


def sanitize_html(value: str | None) -> str:
    if not value:
        return ""
    return nh3.clean(
        value,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer nofollow",
    )


def html_text(value: str | None) -> str:
    if not value:
        return ""
    return BeautifulSoup(value, "html.parser").get_text(" ", strip=True)


def word_count(value: str | None) -> int:
    return len(re.findall(r"\w+", html_text(value)))


def media_url(asset: ImageAsset, thumb: bool = False) -> str | None:
    path = asset.thumbnail_path if thumb else asset.local_path
    if not path:
        return None
    return f"{get_settings().public_base_url.rstrip('/')}/media/{path}"


def credit_line(asset: ImageAsset) -> str:
    lic = asset.license
    text = asset.attribution_text or ""
    if lic and lic.url and lic.name and lic.name not in text:
        text = f"{text} ({lic.name})".strip()
    return text


def render_article_html(
    article: Article, *, for_preview: bool = False, include_header: bool = True, include_featured: bool = True
) -> str:
    """Self-contained HTML for preview, the built-in public API and webhooks.

    WordPress renders its own title and featured image, so its connector turns both off.
    """
    e = html.escape
    parts = ["<article class=\"nw-article\">"]
    if include_header:
        parts.append(f"<header><h1>{e(article.headline)}</h1>")
        if article.subtitle:
            parts.append(f"<p class=\"nw-subtitle\">{e(article.subtitle)}</p>")
        parts.append("</header>")
    img = article.featured_image
    if img is not None and include_featured:
        src = media_url(img) or (img.original_url if for_preview else None)
        if src:
            cap = article.featured_image_caption or img.title or ""
            parts.append(
                f"<figure class=\"nw-featured\"><img src=\"{e(src)}\" alt=\"{e(cap)}\"/>"
                f"<figcaption>{e(cap)}<span class=\"nw-credit\"> {e(credit_line(img))}</span></figcaption></figure>"
            )
    parts.append(f"<div class=\"nw-body\">{sanitize_html(article.body_html)}</div>")
    inline = [ai for ai in article.images if not img or ai.image_asset_id != img.id]
    credits = ([img] if img else []) + [ai.image for ai in inline]
    if credits:
        parts.append("<section class=\"nw-credits\"><h2>Image credits</h2><ul>")
        for c in credits:
            link = c.source_page_url or c.original_url
            label = e(credit_line(c))
            parts.append(f"<li><a href=\"{e(link)}\" rel=\"noopener nofollow\">{label}</a></li>" if link else f"<li>{label}</li>")
        parts.append("</ul></section>")
    if article.sources:
        parts.append("<section class=\"nw-sources\"><h2>Sources</h2><ul>")
        for s in article.sources:
            label = e(s.title or s.url)
            pub = f" — {e(s.publisher)}" if s.publisher else ""
            parts.append(f"<li><a href=\"{e(s.url)}\" rel=\"noopener nofollow\">{label}</a>{pub}</li>")
        parts.append("</ul></section>")
    parts.append("</article>")
    return "".join(parts)


PREVIEW_CSS = """
:root { color-scheme: light dark; --fg:#1b1f24; --muted:#5c6675; --rule:#e2e5ea; --link:#1f5fd1; --bg:#ffffff; }
@media (prefers-color-scheme: dark) { :root { --fg:#ece8e0; --muted:#9aa3b2; --rule:#2c3440; --link:#7ea6ff; --bg:#14191f; } }
* { box-sizing: border-box; }
html, body { margin: 0; background: var(--bg); color: var(--fg); }
body { font-family: "Source Serif 4", "Source Serif Pro", Georgia, Cambria, "Times New Roman",
       "Noto Serif Bengali", SolaimanLipi, "Kohinoor Bangla", "Bangla Sangam MN", "Nirmala UI", Vrinda, serif;
       font-size: 19px; line-height: 1.65; padding: 40px 20px 64px; }
.nw-article { max-width: 700px; margin: 0 auto; }
.nw-article header h1 { font-size: 2.1em; line-height: 1.15; margin: 0 0 .35em; letter-spacing: -0.01em; }
.nw-subtitle { font-size: 1.15em; color: var(--muted); margin: 0 0 1.2em; }
.nw-featured { margin: 1.4em 0; }
.nw-featured img, .nw-body img { max-width: 100%; height: auto; display: block; border-radius: 4px; }
figcaption { font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", "Noto Sans Bengali", "Hind Siliguri", SolaimanLipi, "Kohinoor Bangla", "Nirmala UI", sans-serif; font-size: 13px; color: var(--muted); margin-top: 6px; }
.nw-credit { opacity: .9; }
.nw-body h2 { font-size: 1.35em; margin: 1.6em 0 .5em; }
.nw-body h3 { font-size: 1.15em; margin: 1.4em 0 .4em; }
.nw-body blockquote { margin: 1.2em 0; padding-left: 1em; border-left: 3px solid var(--rule); color: var(--muted); }
.nw-body pre { background: rgba(127,127,127,.12); padding: 12px; border-radius: 4px; overflow-x: auto; font-size: 14px; }
a { color: var(--link); }
hr { border: 0; border-top: 1px solid var(--rule); margin: 2em 0; }
.nw-credits, .nw-sources { font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", "Noto Sans Bengali", "Hind Siliguri", SolaimanLipi, "Kohinoor Bangla", "Nirmala UI", sans-serif; font-size: 14px;
                           border-top: 1px solid var(--rule); margin-top: 2.2em; padding-top: 1em; color: var(--muted); }
.nw-credits h2, .nw-sources h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .06em; margin: 0 0 .6em; }
.nw-credits ul, .nw-sources ul { padding-left: 1.1em; margin: 0; }
@media (max-width: 480px) { body { font-size: 17px; padding: 24px 16px 48px; } .nw-article header h1 { font-size: 1.7em; } }
"""


def preview_document(article: Article) -> str:
    """A complete HTML document (serif reading layout) for the editor's preview iframe."""
    # Articles have no language field; Bangla in the headline or body is enough to tell.
    sample = f"{article.headline} {(article.body_html or '')[:4000]}"
    lang = "bn" if sum("\u0980" <= ch <= "\u09ff" for ch in sample) > 20 else "en"
    return (
        f"<!doctype html><html lang=\"{lang}\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{html.escape(article.headline)} \u2014 preview</title><style>{PREVIEW_CSS}</style></head>"
        f"<body>{render_article_html(article, for_preview=True)}</body></html>"
    )


def publication_checks(article: Article, *, commercial: bool, block_on_markers: bool = True) -> list[dict]:
    """Pre-publication checks. Items with level=error block publishing."""
    out: list[dict] = []
    if not (article.headline or "").strip():
        out.append({"level": "error", "code": "headline_missing", "message": "Headline is required."})
    if word_count(article.body_html) < 20:
        out.append({"level": "error", "code": "body_too_short", "message": "Article body has fewer than 20 words."})
    if block_on_markers and TEMPLATE_MARKER in (article.body_html or ""):
        out.append({"level": "error", "code": "template_markers",
                    "message": "Template instructions ([EDITOR: …]) are still in the text."})
    if not article.sources:
        out.append({"level": "warning", "code": "no_sources", "message": "No source references are attached."})
    if not article.seo_description:
        out.append({"level": "info", "code": "seo_description", "message": "SEO description is empty."})
    images: list[tuple[ImageAsset, bool, str]] = []
    if article.featured_image is not None:
        images.append((article.featured_image, False, "Featured image"))
    for ai in article.images:
        images.append((ai.image, ai.is_modified, f"Image “{(ai.image.title or 'untitled')[:40]}”"))
    for asset, modified, label in images:
        lic = asset.license
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
        ):
            if w.level == "info":
                continue
            out.append({"level": w.level, "code": w.code, "message": f"{label}: {w.message}"})
    return out


def template_html(assignment: EditorialAssignment, references: list[tuple[str, str, str | None]], related: list[str]) -> str:
    """Factual article scaffold. Bracketed instructions must be replaced before publishing."""
    e = html.escape
    parts = [
        f"<p>{TEMPLATE_MARKER} Write the introduction in your own words. Do not copy publisher text.]</p>",
        "<h2>Key facts</h2><ul>",
        f"<li>{TEMPLATE_MARKER} Add a fact you have verified, and name who confirmed it.]</li>",
        f"<li>{TEMPLATE_MARKER} Add another verified fact.]</li></ul>",
        "<h2>Background</h2>",
        f"<p>{TEMPLATE_MARKER} Context from your research notes.]</p>",
    ]
    if assignment.editorial_notes:
        parts.append(f"<blockquote><p>Research notes: {e(assignment.editorial_notes)}</p></blockquote>")
    if references:
        parts.append("<h2>Reporting consulted</h2><ul>")
        for title, url, publisher in references:
            pub = f" ({e(publisher)})" if publisher else ""
            parts.append(f"<li><a href=\"{e(url)}\">{e(title)}</a>{pub}</li>")
        parts.append("</ul>")
    if related:
        parts.append("<h2>Related coverage</h2><ul>" + "".join(f"<li>{e(r)}</li>" for r in related) + "</ul>")
    return "".join(parts)
