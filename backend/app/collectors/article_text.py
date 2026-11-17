"""Full story text from a news article page, read on request.

An editor asks for it from the report's detail panel; nothing is fetched in the background. The page is
fetched like a reader opening the link would: robots.txt is respected (when the source asks for it),
NEWSWIRE's own user agent is sent, no cookies or credentials, and paywalls, logins and bot checks are
reported, never bypassed.

Extraction is deterministic, in this order:

1. `articleBody` from the page's schema.org JSON-LD (NewsArticle, Article, ReportageNewsArticle...).
2. The element marked `itemprop="articleBody"`.
3. The block with the most paragraph text and the fewest links (the classic readability scoring),
   after removing navigation, share bars, "related news", comments and ads.

The result is paragraphs of plain text plus the story's own images and embedded videos, so an editor
can read the whole report and start a draft from it.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from app.collectors import robots
from app.collectors.parsers import parse_datetime
from app.core.http import ResponseTooLarge, UnsafeURLError, describe_error, describe_http_status, safe_request

MAX_PAGE_BYTES = 4_000_000
MIN_BODY_CHARS = 250
MAX_PARAGRAPHS = 400
ARTICLE_TYPES = {"newsarticle", "article", "reportagenewsarticle", "blogposting", "analysisnewsarticle",
                 "opinionnewsarticle", "reviewnewsarticle", "backgroundnewsarticle", "report", "liveblogposting"}
VIDEO_HOSTS = ("youtube.com", "youtube-nocookie.com", "youtu.be", "facebook.com", "fb.watch", "vimeo.com",
               "dailymotion.com", "dai.ly")

# Class/id words of page furniture rather than story text.
_JUNK = re.compile(
    r"(^|[\s_-])(share|sharing|social|related|more-?news|read-?more|also-?read|recommend|comment|disqus|"
    r"advert|ads?|ad-?slot|dfp|gpt|sponsor|promo|newsletter|subscribe|breadcrumb|tags?|tag-?list|keywords|"
    r"footer|header|menu|nav|navbar|sidebar|widget|popup|modal|cookie|banner|print|toolbar|author-?box|"
    r"most-?read|popular|trending|latest|next-?story|prev|pagination|caption-?credit|follow|whatsapp|"
    r"google-?news|app-?download)([\s_-]|$)",
    re.I,
)
_STRIP_TAGS = ("script", "style", "noscript", "template", "form", "button", "input", "select", "textarea",
               "nav", "header", "footer", "aside", "svg", "canvas", "dialog", "object", "embed")
_BLOCKS = ("p", "h2", "h3", "h4", "blockquote", "li", "pre")
_SPACE = re.compile(r"[ \t ​]+")
_BOILERPLATE = re.compile(
    r"^(advertisement|বিজ্ঞাপন|আরও পড়ুন|আরো পড়ুন|আরও পড়ুন\s*:|read more|also read|share|শেয়ার|"
    r"follow us|subscribe|ফলো করুন|সাবস্ক্রাইব)\b",
    re.I,
)


class ArticleFetchError(Exception):
    """The page could not be read; the message says why in words an editor can act on."""


@dataclass
class ArticleText:
    url: str
    title: str | None = None
    paragraphs: list[str] = field(default_factory=list)
    author: str | None = None
    published_at: datetime | None = None
    lead_image: str | None = None
    images: list[dict] = field(default_factory=list)  # {"url", "caption"}
    videos: list[str] = field(default_factory=list)
    method: str = ""  # json_ld | itemprop | density

    @property
    def text(self) -> str:
        return "\n\n".join(self.paragraphs)

    @property
    def char_count(self) -> int:
        return sum(len(p) for p in self.paragraphs)


def _clean(text: str) -> str:
    lines = [_SPACE.sub(" ", ln).strip() for ln in text.replace("\r", "").split("\n")]
    return "\n".join(ln for ln in lines if ln).strip()


def _split_paragraphs(text: str) -> list[str]:
    text = html_lib.unescape(text)  # some sites put escaped HTML (&lt;p&gt;) in articleBody
    if "<" in text and ">" in text:
        soup = BeautifulSoup(text, "html.parser")
        for br in soup.find_all("br"):
            br.replace_with("\n")
        for block in soup.find_all(_BLOCKS):
            block.insert_after("\n")
        text = soup.get_text("")
    parts = re.split(r"\n\s*\n|\n", text.replace("\r", ""))
    paras = [p for p in (_SPACE.sub(" ", x).strip() for x in parts) if p]
    if len(paras) == 1 and len(paras[0]) > 1200:
        paras = _regroup(paras[0])
    return paras


_SENTENCE_END = re.compile(r"(?<=[।?!])\s+|(?<=[.?!][\"'”’)])\s+|(?<=[a-z0-9)][.?!])\s+(?=[A-Z\u0980-\u09FF\"“‘(])")


def _regroup(text: str, per_paragraph: int = 3) -> list[str]:
    """A story some sites run into one block (in JSON-LD): three sentences per paragraph, so it reads."""
    sentences = [x.strip() for x in _SENTENCE_END.split(text) if x and x.strip()]
    if len(sentences) < 4:
        return [text]
    return [" ".join(sentences[i:i + per_paragraph]) for i in range(0, len(sentences), per_paragraph)]


# ---------------------------------------------------------------------------------------------
# 1. JSON-LD


def _ld_objects(soup: BeautifulSoup):
    for tag in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        raw = tag.string or tag.get_text() or ""
        try:
            data = json.loads(raw.strip(), strict=False)
        except ValueError:
            continue
        stack = [data]
        while stack:
            obj = stack.pop()
            if isinstance(obj, list):
                stack.extend(obj)
            elif isinstance(obj, dict):
                yield obj
                if isinstance(obj.get("@graph"), list):
                    stack.extend(obj["@graph"])
                if isinstance(obj.get("mainEntity"), (dict, list)):
                    stack.append(obj["mainEntity"])


def _is_article(obj: dict) -> bool:
    types = obj.get("@type")
    types = types if isinstance(types, list) else [types]
    return any(isinstance(t, str) and t.lower() in ARTICLE_TYPES for t in types)


def _ld_name(value) -> str | None:
    if isinstance(value, list):
        names = [n for n in (_ld_name(v) for v in value) if n]
        return ", ".join(dict.fromkeys(names)) or None
    if isinstance(value, dict):
        value = value.get("name")
    return value.strip()[:300] if isinstance(value, str) and value.strip() else None


def _ld_image(value) -> str | None:
    if isinstance(value, list) and value:
        return _ld_image(value[0])
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl")
    return value if isinstance(value, str) and value.startswith(("http://", "https://")) else None


# ---------------------------------------------------------------------------------------------
# 3. Density scoring


def _is_junk(tag: Tag) -> bool:
    if tag.attrs is None:
        return False
    if tag.get("aria-hidden") == "true" or tag.get("hidden") is not None:
        return True
    role = (tag.get("role") or "").lower()
    if role in ("navigation", "banner", "contentinfo", "complementary", "dialog", "menu"):
        return True
    words = " ".join(tag.get("class") or []) + " " + (tag.get("id") or "")
    if not words.strip():
        return False
    # Words like "article-header" or "news-details" describe the story itself.
    if re.search(r"article|story|content|details|body|post|news-?text|entry", words, re.I) and not re.search(
        r"related|more|share|comment|recommend|latest|popular|tag", words, re.I
    ):
        return False
    return bool(_JUNK.search(words))


def _strip_furniture(root: Tag) -> None:
    for c in root.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for t in root.find_all(_STRIP_TAGS):
        t.decompose()
    # Never remove the story itself, even when the site wraps it in a "widget" or "sidebar" box.
    protected: set[int] = set()
    marked = root.find(attrs={"itemprop": "articleBody"})
    if marked is not None:
        protected = {id(marked)} | {id(a) for a in marked.parents}
    para_total = sum(_text_len(p) for p in root.find_all("p")) or 1
    for t in root.find_all(True):
        if getattr(t, "decomposed", False) or t.parent is None or id(t) in protected:
            continue  # inside something already removed, or holds the story
        if _is_junk(t) and sum(_text_len(p) for p in t.find_all("p")) < 0.4 * para_total:
            t.decompose()


def _text_len(tag: Tag) -> int:
    return len(_SPACE.sub(" ", tag.get_text(" ", strip=True)))


def _link_density(tag: Tag) -> float:
    total = _text_len(tag) or 1
    links = sum(len(a.get_text(" ", strip=True)) for a in tag.find_all("a"))
    return links / total


def _best_container(root: Tag) -> Tag | None:
    scores: dict[int, tuple[Tag, float]] = {}

    def add(tag: Tag | None, points: float) -> None:
        if tag is None or tag.name in ("html", "[document]"):
            return
        key = id(tag)
        prev = scores.get(key)
        scores[key] = (tag, (prev[1] if prev else 0.0) + points)

    candidates = root.find_all(["p", "div", "section", "span", "font"])
    for node in candidates:
        if node.name in ("div", "section", "span", "font"):
            # Only text-holding blocks (sites that write paragraphs as <div>s or <br>-separated text).
            own = "".join(s for s in node.find_all(string=True, recursive=False) if isinstance(s, NavigableString))
            length = len(_SPACE.sub(" ", own).strip())
            if length < 80:
                continue
        else:
            length = _text_len(node)
            if length < 25:
                continue
        points = 1 + min(length / 100, 3) + node.get_text().count("।") + node.get_text().count(", ") * 0.5
        add(node.parent, points)
        add(node.parent.parent if node.parent else None, points / 2)
    if not scores:
        return None
    best, best_score = None, 0.0
    for tag, score in scores.values():
        score *= 1 - min(_link_density(tag), 0.9)
        if score > best_score:
            best, best_score = tag, score
    return best


def _paragraphs_from(container: Tag) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def push(text: str) -> None:
        text = _SPACE.sub(" ", text).strip()
        if len(text) < 2 or text in seen or _BOILERPLATE.match(text):
            return
        seen.add(text)
        out.append(text)

    blocks = container.find_all(_BLOCKS)
    if blocks and sum(_text_len(b) for b in blocks) >= 0.5 * _text_len(container):
        for b in blocks:
            if b.find_parent(_BLOCKS) is not None:  # nested (a <p> inside a <blockquote>)
                continue
            if _link_density(b) > 0.6 and _text_len(b) < 200:
                continue
            push(b.get_text(" ", strip=True))
    else:
        for part in container.get_text("\n").split("\n"):
            push(part)
    return out[:MAX_PARAGRAPHS]


# ---------------------------------------------------------------------------------------------
# Media


def _media(container: Tag | None, base_url: str, lead: str | None) -> tuple[list[dict], list[str]]:
    images: list[dict] = []
    videos: list[str] = []
    if container is None:
        return images, videos
    seen = {lead} if lead else set()
    for img in container.find_all("img"):
        src = img.get("data-src") or img.get("data-original") or img.get("data-lazy-src") or img.get("src") or ""
        if not src or src.startswith("data:"):
            continue
        src = urljoin(base_url, src.strip())
        if src in seen or not src.startswith(("http://", "https://")):
            continue
        w = img.get("width")
        if w and str(w).isdigit() and int(w) < 120:
            continue  # icons, avatars, tracking pixels
        seen.add(src)
        fig = img.find_parent("figure")
        cap = fig.find("figcaption") if fig else None
        images.append({"url": src, "caption": cap.get_text(" ", strip=True)[:500] if cap else (img.get("alt") or "")[:500]})
        if len(images) >= 12:
            break
    for frame in container.find_all(["iframe", "video", "source"]):
        src = urljoin(base_url, (frame.get("src") or frame.get("data-src") or "").strip())
        host = (urlsplit(src).hostname or "").lower()
        if src.startswith(("http://", "https://")) and any(host == h or host.endswith("." + h) for h in VIDEO_HOSTS):
            if src not in videos:
                videos.append(src)
    return images, videos[:6]


# ---------------------------------------------------------------------------------------------


def extract(html: bytes | str, url: str) -> ArticleText:
    soup = BeautifulSoup(html, "html.parser")
    result = ArticleText(url=url)

    def meta(name: str) -> str | None:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        value = tag.get("content") if tag else None
        return value.strip() if isinstance(value, str) and value.strip() else None

    result.title = meta("og:title") or (soup.title.get_text(strip=True) if soup.title else None)
    result.lead_image = meta("og:image")
    if result.lead_image:
        result.lead_image = urljoin(url, result.lead_image)
    result.author = meta("author") or meta("article:author")
    if result.author and result.author.startswith("http"):
        result.author = None
    result.published_at = parse_datetime(meta("article:published_time"))

    body_text: str | None = None
    for obj in _ld_objects(soup):
        if not _is_article(obj):
            continue
        result.title = result.title or _ld_name(obj.get("headline"))
        result.author = result.author or _ld_name(obj.get("author"))
        result.published_at = result.published_at or parse_datetime(obj.get("datePublished"))
        result.lead_image = result.lead_image or _ld_image(obj.get("image"))
        body = obj.get("articleBody")
        if isinstance(body, str) and len(body.strip()) >= MIN_BODY_CHARS and len(body) > len(body_text or ""):
            body_text = body

    root = soup.body or soup
    _strip_furniture(root)
    container = root.find(attrs={"itemprop": "articleBody"})
    dom_paras: list[str] = []
    dom_method = ""
    if container is not None:
        dom_paras, dom_method = _paragraphs_from(container), "itemprop"
    if sum(len(p) for p in dom_paras) < MIN_BODY_CHARS:
        container = _best_container(root)
        dom_paras, dom_method = (_paragraphs_from(container), "density") if container is not None else ([], "")
    dom_chars = sum(len(p) for p in dom_paras)

    ld_paras = _split_paragraphs(body_text)[:MAX_PARAGRAPHS] if body_text else []
    ld_chars = sum(len(p) for p in ld_paras)
    # JSON-LD is the publisher's own copy of the text, but some sites truncate it or run it into one
    # paragraph; the page's paragraphs win when they hold at least as much text, split better.
    if ld_chars >= MIN_BODY_CHARS and not (dom_chars >= 0.8 * ld_chars and len(dom_paras) > len(ld_paras)):
        result.paragraphs, result.method = ld_paras, "json_ld"
    elif dom_chars >= MIN_BODY_CHARS:
        result.paragraphs, result.method = dom_paras, dom_method
    result.images, result.videos = _media(container, url, result.lead_image)
    if result.title:
        result.title = result.title[:1000]
    return result


def fetch_article(client: httpx.Client, url: str, *, respect_robots: bool = True) -> ArticleText:
    if respect_robots:
        verdict = robots.check(client, url)
        if not verdict.allowed:
            raise ArticleFetchError(verdict.reason or "Disallowed by the publisher's robots.txt")
    try:
        resp = safe_request(
            client, "GET", url, max_bytes=MAX_PAGE_BYTES,
            headers={"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5", "Accept-Language": "bn,en;q=0.8"},
        )
    except (UnsafeURLError, ResponseTooLarge, httpx.HTTPError) as exc:
        raise ArticleFetchError(describe_error(exc)) from exc
    if resp.status_code != 200:
        raise ArticleFetchError(describe_http_status(resp))
    ctype = resp.headers.get("content-type", "")
    if ctype and "html" not in ctype:
        raise ArticleFetchError(f"The link is not a web page ({ctype.split(';')[0]})")
    article = extract(resp.content, resp.url)
    if not article.paragraphs:
        raise ArticleFetchError(
            "No story text was found on the page. It may be a video or photo story, a live page, or text "
            "the site only shows after running JavaScript."
        )
    return article
