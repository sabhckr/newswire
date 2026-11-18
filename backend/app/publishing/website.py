"""Publish to any website through its own admin login: the login address, the admin's email or
username, and the password.

* **WordPress** is recognised behind the login. NEWSWIRE signs in once, creates an application
  password named "NEWSWIRE" for that account and publishes through the WordPress REST API (the
  same code as a WordPress destination). If the application password is later revoked, the stored
  login creates a new one.
* **Any other site** (a custom admin panel) is published by a robot browser (Playwright +
  Chromium) that signs in and fills in the site's own "new post" form. An administrator shows it
  the form once (*Teach*): a browser window opens on this computer, signs in, and asks them to
  click the title box, the text box, the image upload, the Publish button and so on. The browser's
  session (cookies) is kept, encrypted, so it does not sign in again every time; when the session
  ends it signs in with the stored password.

Stored for a `website` destination:

* ``base_url``: the admin login address; ``username``: the admin email or username.
* secret (Fernet-encrypted JSON): ``password``, and ``app_password`` (WordPress) or
  ``storage_state`` (robot browser cookies).
* ``config``: ``platform`` ("wordpress" | "custom"), ``wp_base``/``wp_user`` (WordPress),
  ``recipe`` (what was taught), ``body_format`` ("auto" | "html" | "text"), ``taught_at``.

The robot cannot pass captchas or two-step codes by itself. During *Teach* the person can complete
them in the window; publishing then reuses that session for as long as the site keeps it.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from app.articles.content import render_article_html
from app.core.config import get_settings
from app.core.http import SafeResponse, UnsafeURLError, safe_request, validate_url
from app.models import Article, PublishingDestination
from app.publishing.connectors import PublishError, RemoteResult, WordPressConnector
from app.publishing.social import load_secrets, save_secrets, set_config

log = logging.getLogger(__name__)

OVERLAY_JS = (Path(__file__).with_name("teach_overlay.js")).read_text(encoding="utf-8")
NAV_TIMEOUT_MS = 45_000
SETTLE_TIMEOUT_MS = 20_000
HUMAN_LOGIN_SECONDS = 5 * 60
TEACH_SECONDS = 20 * 60
BODY_FORMATS = ("auto", "html", "text")

# One browser per destination at a time (teaching and publishing share the stored session).
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(dest_id) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(str(dest_id), threading.Lock())


class SetupError(Exception):
    """A problem the administrator has to fix (wrong password, captcha, disabled API...)."""


def site_root(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def check_url(url: str) -> str:
    try:
        return validate_url(url)
    except UnsafeURLError as exc:
        raise SetupError(str(exc)) from exc


# --- WordPress behind the admin login -----------------------------------------------------------------


def _json_or_none(resp: SafeResponse) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None


def detect_wordpress(client: httpx.Client, login_url: str) -> str | None:
    """The WordPress site root when the login belongs to WordPress, else None."""
    root = site_root(login_url)
    m = re.match(r"^(.*?)/(?:wp-login\.php|wp-admin)(?:/|$)", urlsplit(login_url).path)
    candidates = list(dict.fromkeys(([root + m.group(1)] if m else []) + [root]))
    for base in candidates:
        try:
            resp = safe_request(client, "GET", base.rstrip("/") + "/wp-json/?_fields=namespaces",
                                headers={"Accept": "application/json"}, max_bytes=3_000_000)
        except Exception:  # noqa: BLE001 - not WordPress, or not reachable this way
            continue
        data = _json_or_none(resp) if resp.status_code == 200 else None
        if isinstance(data, dict) and "wp/v2" in (data.get("namespaces") or []):
            return base.rstrip("/")
    return None


def _looks_blocked(text: str) -> str | None:
    low = text.lower()
    if any(k in low for k in ("g-recaptcha", "h-captcha", "hcaptcha", "cf-turnstile", "captcha")):
        return "captcha"
    if any(k in low for k in ("two-factor", "two factor", "2fa", "authentication code", "verification code", "one-time")):
        return "two-step"
    return None


def wordpress_setup(client: httpx.Client, wp_base: str, login: str, password: str) -> dict:
    """Sign in to WordPress like a browser and create an application password for NEWSWIRE."""
    login_url = wp_base + "/wp-login.php"
    host = urlsplit(wp_base).hostname or ""
    client.cookies.set("wordpress_test_cookie", "WP Cookie check", domain=host, path="/")
    resp = safe_request(client, "POST", login_url, follow_redirects=False, max_bytes=3_000_000, data={
        "log": login, "pwd": password, "wp-submit": "Log In", "redirect_to": wp_base + "/wp-admin/", "testcookie": "1",
    })
    logged_in = any(c.name.startswith("wordpress_logged_in") for c in client.cookies.jar)
    if not logged_in:
        blocked = _looks_blocked(resp.text)
        if blocked == "captcha":
            raise SetupError("The WordPress login has a captcha, so NEWSWIRE cannot sign in by itself. Create an "
                             "application password in WordPress (Users → Profile) and add the site as a WordPress "
                             "destination instead.")
        if blocked == "two-step":
            raise SetupError("This WordPress account uses two-step sign-in. Create an application password in "
                             "WordPress (Users → Profile) and add the site as a WordPress destination instead.")
        raise SetupError("WordPress did not accept that email/username and password.")
    nonce_resp = safe_request(client, "GET", wp_base + "/wp-admin/admin-ajax.php?action=rest-nonce",
                              follow_redirects=False, max_bytes=10_000)
    nonce = nonce_resp.text.strip() if nonce_resp.status_code == 200 else ""
    if not re.fullmatch(r"[0-9a-f]{6,20}", nonce):
        raise SetupError("Signed in, but WordPress did not allow access to its API from this account.")
    api = {"X-WP-Nonce": nonce, "Accept": "application/json"}
    me_resp = safe_request(client, "GET", wp_base + "/wp-json/wp/v2/users/me?context=edit", headers=api)
    me = _json_or_none(me_resp) or {}
    if me_resp.status_code >= 400 or not isinstance(me, dict) or not me.get("username"):
        raise SetupError("Signed in, but WordPress did not say who this account is (HTTP "
                         f"{me_resp.status_code}). A security plugin may be blocking the REST API.")
    caps = me.get("capabilities") or {}
    if isinstance(caps, dict) and caps and not caps.get("publish_posts"):
        raise SetupError(f"The WordPress account “{me['username']}” cannot publish posts. Use an Administrator, "
                         "Editor or Author account.")
    ap_resp = safe_request(client, "POST", wp_base + "/wp-json/wp/v2/users/me/application-passwords",
                           headers={**api, "Content-Type": "application/json"},
                           content=json.dumps({"name": f"NEWSWIRE {time.strftime('%Y-%m-%d')}"}).encode())
    ap = _json_or_none(ap_resp) or {}
    if ap_resp.status_code >= 400 or not isinstance(ap, dict) or not ap.get("password"):
        code = ap.get("code") if isinstance(ap, dict) else None
        if code == "application_passwords_disabled" or (code or "").endswith("insecure") or ap_resp.status_code == 501:
            raise SetupError("WordPress has application passwords turned off for this site. They need the site to "
                             "use https (or a plugin has disabled them).")
        raise SetupError(f"WordPress did not create an application password (HTTP {ap_resp.status_code}"
                         f"{': ' + str(ap.get('message')) if isinstance(ap, dict) and ap.get('message') else ''}).")
    return {"username": me["username"], "app_password": ap["password"], "name": me.get("name") or me["username"]}


class WordPressLoginConnector(WordPressConnector):
    """WordPress found behind an admin login. A rejected application password is replaced once,
    using the stored login, so publishing keeps working after someone revokes it."""

    def __init__(self, dest: PublishingDestination):
        cfg, sec = dest.config or {}, load_secrets(dest)
        super().__init__(dest, base_url=cfg.get("wp_base"), username=cfg.get("wp_user"), password=sec.get("app_password"))
        self.wp_base = cfg.get("wp_base")
        self._renewed = False

    def _call(self, client, method, path, **kwargs):
        try:
            return super()._call(client, method, path, **kwargs)
        except PublishError as exc:
            if self._renewed or "rejected the credentials" not in str(exc):
                raise
            self._renewed = True
            sec = load_secrets(self.dest)
            if not sec.get("password"):
                raise
            try:
                info = wordpress_setup(client, self.wp_base, self.dest.username or "", sec["password"])
            except (SetupError, httpx.HTTPError) as setup_exc:
                raise PublishError(f"WordPress rejected NEWSWIRE's access and signing in again failed: {setup_exc}",
                                   retryable=False) from setup_exc
            save_secrets(self.dest, {**sec, "app_password": info["app_password"]})
            set_config(self.dest, wp_user=info["username"])
            self._authorize(info["username"], info["app_password"])
            return super()._call(client, method, path, **kwargs)


# --- setting a website destination up -------------------------------------------------------------------


def setup_website(dest: PublishingDestination, password: str, client: httpx.Client) -> str:
    """Find out what kind of site is behind the login and prepare publishing. Returns a sentence."""
    login_url = check_url(dest.base_url or "")
    sec = load_secrets(dest)
    wp_base = detect_wordpress(client, login_url)
    if wp_base:
        info = wordpress_setup(client, wp_base, dest.username or "", password)
        save_secrets(dest, {"password": password, "app_password": info["app_password"]})
        set_config(dest, platform="wordpress", wp_base=wp_base, wp_user=info["username"], recipe=None, taught_at=None)
        return f"WordPress found. Publishing is set up as “{info['name']}”."
    keep = {k: sec.get(k) for k in ("storage_state",) if sec.get(k)}
    save_secrets(dest, {"password": password, **keep})
    set_config(dest, platform="custom", wp_base=None, wp_user=None)
    if (dest.config or {}).get("recipe"):
        return "Login saved."
    return "Custom admin panel. Next: click Teach and show NEWSWIRE where to type the post."


def website_problem(dest: PublishingDestination, mode: str) -> str | None:
    cfg = dest.config or {}
    if cfg.get("platform") == "wordpress":
        return None
    if not (cfg.get("recipe") or {}).get("new_post_url"):
        return "Show NEWSWIRE the website's post form first: Publishing → Destinations → Teach."
    if mode == "draft":
        return "NEWSWIRE fills in this website's own post form, so it can only publish (no drafts)."
    return None


def website_connector(dest: PublishingDestination):
    platform = (dest.config or {}).get("platform")
    if platform == "wordpress":
        return WordPressLoginConnector(dest)
    if platform == "custom":
        return RobotConnector(dest)
    raise PublishError(f"{dest.name} is not set up yet. Edit it and save the login again.", retryable=False)


def website_summary(dest: PublishingDestination) -> dict:
    cfg = dest.config or {}
    recipe = cfg.get("recipe") or {}
    return {
        "platform": cfg.get("platform"),
        "taught": bool(recipe.get("new_post_url")),
        "taught_at": cfg.get("taught_at"),
        "new_post_url": recipe.get("new_post_url"),
        "fields": sorted((recipe.get("fields") or {}).keys()),
        "body_format": cfg.get("body_format") or "auto",
        "wp_user": cfg.get("wp_user"),
    }


# --- the robot browser ---------------------------------------------------------------------------------

PASSWORD_FIELD = "input[type=password]:visible"
USER_FIELDS = ("input[type=email]:visible", "input[autocomplete=username]:visible", "input[name*=user i]:visible",
               "input[name*=email i]:visible", "input[name*=login i]:visible", "input[type=text]:visible",
               "input:not([type]):visible")
SUBMIT_FIELDS = ("button[type=submit]:visible", "input[type=submit]:visible", "button:not([type]):visible",
                 "[role=button]:visible")
ERROR_TEXT = ("[role=alert]:visible, .alert-danger:visible, .alert-error:visible, .error:visible, .errors:visible, "
              ".invalid-feedback:visible, .text-danger:visible, .notice-error:visible, .help-block.error:visible, "
              ".field-error:visible, .is-invalid:visible")


def _python() -> str:
    """This NEWSWIRE's own Python, quoted, so the install hint works as typed on macOS, Linux and Windows."""
    return f'"{sys.executable}"' if " " in sys.executable else sys.executable


def _sync_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - installed with the backend requirements
        raise PublishError("The robot browser is not installed on this computer. In a terminal on this computer, run: "
                           f"{_python()} -m pip install playwright  and then  {_python()} -m playwright install chromium"
                           "  — then try again.", retryable=False) from exc
    return sync_playwright


def _launch(p, *, headless: bool):
    try:
        return p.chromium.launch(headless=headless)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "Executable doesn't exist" in msg or "playwright install" in msg:
            raise PublishError("The robot browser is not installed on this computer. In a terminal on this computer, "
                               f"run: {_python()} -m playwright install chromium  — then try again.",
                               retryable=False) from exc
        if not headless and ("display" in msg.lower() or "headed" in msg.lower()):
            raise PublishError("Teaching needs NEWSWIRE running on a computer with a screen (it opens a browser "
                               "window). Run it on your Mac or PC for this one-time step.", retryable=False) from exc
        raise


def _visible(locator) -> bool:
    try:
        return locator.count() > 0 and locator.first.is_visible()
    except Exception:  # noqa: BLE001
        return False


def needs_login(page) -> bool:
    return _visible(page.locator(PASSWORD_FIELD))


def _first(page_or_scope, selectors) -> Any | None:
    for sel in selectors:
        loc = page_or_scope.locator(sel)
        if _visible(loc):
            return loc.first
    return None


def _settle(page, ms: int = SETTLE_TIMEOUT_MS) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=ms)
    except Exception:  # noqa: BLE001 - busy pages never go idle; carry on
        pass


def _blocked_reason(page) -> str | None:
    if _visible(page.locator("iframe[src*=recaptcha], iframe[src*=hcaptcha], iframe[src*=turnstile], .g-recaptcha, "
                             ".h-captcha, .cf-turnstile")):
        return "captcha"
    if _visible(page.locator("input[autocomplete=one-time-code], input[name*=otp i], input[name*=2fa i], "
                             "input[name*=token i][inputmode=numeric]")):
        return "two-step"
    return None


def login(page, login_url: str, username: str, password: str) -> None:
    """Sign in on the login page. Raises PublishError with a plain explanation when it cannot."""
    page.goto(login_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    _settle(page, 8_000)
    if not needs_login(page):
        return  # the stored session is still signed in
    pw = page.locator(PASSWORD_FIELD).first
    form = pw.locator("xpath=ancestor::form[1]")
    scope = form if form.count() else page
    user = None
    # A username/email box shown on the same form as the password (not a search box elsewhere).
    for sel in USER_FIELDS:
        loc = scope.locator(sel)
        if _visible(loc):
            user = loc.first
            break
    if user is not None:
        user.fill(username)
    pw.fill(password)
    submit = _first(scope, SUBMIT_FIELDS)
    if submit is not None:
        submit.click()
    else:
        pw.press("Enter")
    try:
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
    except Exception:  # noqa: BLE001
        pass
    _settle(page, 10_000)
    if needs_login(page):
        reason = _blocked_reason(page)
        if reason == "captcha":
            raise PublishError("The website's login asks for a captcha. Click Teach in NEWSWIRE and complete it in "
                               "the window once; NEWSWIRE then keeps that session.", retryable=False)
        err = page.locator(ERROR_TEXT)
        detail = err.first.inner_text().strip()[:200] if _visible(err) else ""
        raise PublishError("The website did not accept the admin email and password"
                           + (f": {detail}" if detail else ". Check them under Edit."), retryable=False)
    if _blocked_reason(page) == "two-step":
        raise PublishError("The website asks for a two-step code. Click Teach in NEWSWIRE and enter the code in the "
                           "window once; NEWSWIRE then keeps that session.", retryable=False)


def _resolve(page, field: dict):
    """A locator for a taught field. Falls back to the visible text of a taught button."""
    sel = field.get("selector") or ""
    loc = page.locator(sel) if sel else None
    if loc is not None and _visible(loc):
        return loc.first
    text = (field.get("text") or "").strip()
    if text and field.get("tag") in ("button", "a", "input"):
        by_text = page.get_by_role("button", name=text)
        if _visible(by_text):
            return by_text.first
        by_text = page.locator(f"text={json.dumps(text)}")
        if _visible(by_text):
            return by_text.first
    if loc is not None and loc.count():
        return loc.first  # present but hidden (a styled file input)
    return None


def article_text(html_body: str) -> str:
    soup = BeautifulSoup(html_body, "html.parser")
    blocks = [b.get_text(" ", strip=True) for b in soup.find_all(["p", "h2", "h3", "h4", "li", "blockquote"])]
    text = "\n\n".join(b for b in blocks if b)
    return text or soup.get_text("\n", strip=True)


PASTE_JS = """(el, {html, text}) => {
  el.focus();
  const range = document.createRange();
  range.selectNodeContents(el);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  const data = new DataTransfer();
  data.setData('text/html', html);
  data.setData('text/plain', text);
  const handled = !el.dispatchEvent(new ClipboardEvent('paste', {clipboardData: data, bubbles: true, cancelable: true}));
  if (!handled) document.execCommand('insertHTML', false, html);
  if (!el.innerText.trim()) {
    el.innerHTML = html;
    el.dispatchEvent(new InputEvent('input', {bubbles: true}));
  }
  return el.innerText.length;
}"""

RICH_TEXTAREA_JS = """(el, html) => {
  const id = el.id;
  if (window.tinymce && id && window.tinymce.get(id)) { const ed = window.tinymce.get(id); ed.setContent(html); ed.save(); return 'tinymce'; }
  if (window.CKEDITOR && id && window.CKEDITOR.instances[id]) { window.CKEDITOR.instances[id].setData(html); return 'ckeditor'; }
  if (window.jQuery && window.jQuery(el).data && window.jQuery(el).data('summernote')) { window.jQuery(el).summernote('code', html); return 'summernote'; }
  return '';
}"""


def _type_into(loc, value: str) -> None:
    kind = loc.evaluate("e => e.isContentEditable ? 'editable' : e.tagName.toLowerCase()")
    if kind == "editable":
        loc.click()
        loc.press("ControlOrMeta+a")
        loc.page.keyboard.insert_text(value)
    elif kind == "select":
        try:
            loc.select_option(label=value)
        except Exception:  # noqa: BLE001 - no such option; leave the site's default
            pass
    else:
        loc.fill(value)


def _fill_body(page, field: dict, html_body: str, fmt: str) -> None:
    text = article_text(html_body)
    if field.get("frame"):
        frame_el = _resolve(page, field)
        frame = frame_el.content_frame() if frame_el is not None else None
        if frame is None:
            raise PublishError("The article text box was not found (it was inside a frame). Click Teach again.",
                               retryable=False)
        body = frame.locator("body")
        body.evaluate(PASTE_JS, {"html": html_body if fmt != "text" else "", "text": text})
        return
    loc = _resolve(page, field)
    if loc is None:
        raise PublishError("The article text box was not found on the post form. The site may have changed: click "
                           "Teach again.", retryable=False)
    info = loc.evaluate("e => ({tag: e.tagName.toLowerCase(), editable: e.isContentEditable})")
    if info["editable"]:
        loc.evaluate(PASTE_JS, {"html": html_body if fmt != "text" else text.replace("\n", "<br>"), "text": text})
        return
    if info["tag"] == "iframe":
        frame = loc.content_frame()
        frame.locator("body").evaluate(PASTE_JS, {"html": html_body, "text": text})
        return
    if info["tag"] == "textarea" and fmt != "text" and loc.evaluate(RICH_TEXTAREA_JS, html_body):
        return
    loc.fill(html_body if fmt == "html" else text)


def _attach_image(page, field: dict, path: Path) -> None:
    loc = _resolve(page, field)
    if loc is None:
        raise PublishError("The image upload on the post form was not found. Click Teach again, or Skip the image "
                           "step.", retryable=False)
    is_file = loc.evaluate("e => e.tagName === 'INPUT' && e.type === 'file'")
    if is_file:
        loc.set_input_files(str(path))
    else:
        with page.expect_file_chooser(timeout=15_000) as chooser:
            loc.click()
        chooser.value.set_files(str(path))
    _settle(page, 15_000)


def _screenshot(page, dest_id) -> str | None:
    try:
        folder = get_settings().logs_dir / "robot"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{dest_id}-{time.strftime('%Y%m%d-%H%M%S')}.png"
        page.screenshot(path=str(path), full_page=True)
        return str(path.relative_to(get_settings().storage_dir))
    except Exception:  # noqa: BLE001
        return None


class RobotConnector:
    """Fills in a custom admin panel's post form, as taught (see module docstring)."""

    supports_draft = False

    def __init__(self, dest: PublishingDestination, *, headless: bool = True):
        cfg, sec = dest.config or {}, load_secrets(dest)
        self.dest = dest
        self.recipe = cfg.get("recipe") or {}
        self.login_url = dest.base_url or ""
        self.username = dest.username or ""
        self.password = sec.get("password") or ""
        self.body_format = cfg.get("body_format") if cfg.get("body_format") in BODY_FORMATS else "auto"
        self.headless = headless

    @contextmanager
    def _browser(self) -> Iterator[Any]:
        lock = _lock(self.dest.id)
        if not lock.acquire(timeout=10 * 60):
            raise PublishError("The robot browser is busy with this website (teaching or another post); "
                               "NEWSWIRE will try again.")
        try:
            with _sync_playwright()() as p:
                browser = _launch(p, headless=self.headless)
                try:
                    state = load_secrets(self.dest).get("storage_state")
                    ctx = browser.new_context(storage_state=state or None, accept_downloads=False)
                    ctx.set_default_timeout(NAV_TIMEOUT_MS)
                    page = ctx.new_page()
                    yield page
                    sec = load_secrets(self.dest)
                    save_secrets(self.dest, {**sec, "storage_state": ctx.storage_state()})
                finally:
                    browser.close()
        finally:
            lock.release()

    def _open_form(self, page) -> None:
        url = self.recipe.get("new_post_url")
        if not url:
            raise PublishError("Show NEWSWIRE the website's post form first: Publishing → Destinations → Teach.",
                               retryable=False)
        page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        _settle(page)
        if needs_login(page) or urlsplit(page.url).path.rstrip("/") == urlsplit(self.login_url).path.rstrip("/"):
            login(page, self.login_url, self.username, self.password)
            page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            _settle(page)
        if needs_login(page):
            raise PublishError("The website keeps asking to sign in. Check the admin login under Edit.", retryable=False)

    def publish(self, article, publication, mode, client, options=None) -> RemoteResult:
        if mode == "draft":
            raise PublishError("NEWSWIRE fills in this website's own post form, so it can only publish (no drafts).",
                               retryable=False)
        if publication and publication.remote_status == "published":
            raise PublishError("This article is already on the website. NEWSWIRE can add new posts there but not "
                               "change existing ones: edit the post in the website's admin panel.", retryable=False)
        fields = self.recipe.get("fields") or {}
        if not fields.get("title") or not fields.get("body") or not self.recipe.get("submit"):
            raise PublishError("NEWSWIRE has not been shown the title box, text box and Publish button yet. Click "
                               "Teach.", retryable=False)
        html_body = render_article_html(article, include_header=False, include_featured=False)
        img = article.featured_image
        img_path = get_settings().images_dir / img.local_path if img is not None and img.local_path else None
        with self._browser() as page:
            try:
                self._open_form(page)
                title = _resolve(page, fields["title"])
                if title is None:
                    raise PublishError("The title box was not found on the post form. The site may have changed: "
                                       "click Teach again.", retryable=False)
                _type_into(title, article.headline)
                _fill_body(page, fields["body"], html_body, self.body_format)
                if fields.get("excerpt") and (article.excerpt or article.seo_description):
                    loc = _resolve(page, fields["excerpt"])
                    if loc is not None:
                        _type_into(loc, article.excerpt or article.seo_description or "")
                if fields.get("tags") and article.tags:
                    loc = _resolve(page, fields["tags"])
                    if loc is not None:
                        _type_into(loc, ", ".join(t.name for t in article.tags))
                if fields.get("category") and article.category:
                    loc = _resolve(page, fields["category"])
                    if loc is not None:
                        _type_into(loc, article.category.name)
                if fields.get("image") and img_path is not None and img_path.is_file():
                    _attach_image(page, fields["image"], img_path)
                submit = _resolve(page, self.recipe["submit"])
                if submit is None:
                    raise PublishError("The Publish button was not found on the post form. Click Teach again.",
                                       retryable=False)
                before = page.url
                submit.click()
                try:
                    page.wait_for_url(lambda u: u != before, timeout=15_000)
                except Exception:  # noqa: BLE001 - single-page admin panels stay on one address
                    pass
                _settle(page)
                if needs_login(page):
                    raise PublishError("The website signed NEWSWIRE out while posting; it will try again.")
                errors = page.locator(ERROR_TEXT)
                if page.url == before and _visible(errors):
                    raise PublishError(f"The website did not accept the post: {errors.first.inner_text().strip()[:250]}",
                                       retryable=False)
                link = self._find_link(page, article.headline)
                return RemoteResult(link or page.url, link or page.url, "published",
                                    {"landed_on": page.url, "robot": True})
            except PublishError as exc:
                shot = _screenshot(page, self.dest.id)
                raise PublishError(f"{exc}" + (f" (screenshot: storage/{shot})" if shot else ""), exc.retryable) from exc
            except Exception as exc:  # noqa: BLE001 - browser timeouts and crashes
                shot = _screenshot(page, self.dest.id)
                raise PublishError(f"The robot browser could not finish: {str(exc).splitlines()[0][:200]}"
                                   + (f" (screenshot: storage/{shot})" if shot else "")) from exc

    @staticmethod
    def _find_link(page, headline: str) -> str | None:
        """The public link of the new post, when the page after Publish shows one."""
        candidates = [page.get_by_role("link", name=headline[:80])]
        candidates += [page.get_by_role("link", name=re.compile(r"^(view|see|open|preview)\b.*(post|article|news)?", re.I))]
        for loc in candidates:
            try:
                if loc.count():
                    href = loc.first.get_attribute("href")
                    if href and not href.startswith(("#", "javascript:")):
                        return urljoin(page.url, href)
            except Exception:  # noqa: BLE001
                continue
        return None

    def unpublish(self, article, publication, client) -> RemoteResult:
        raise PublishError("NEWSWIRE can only add posts to this website. Delete or hide the post in the website's "
                           "admin panel.", retryable=False)

    def status(self, article, publication, client) -> RemoteResult:
        return RemoteResult(publication.remote_id, publication.remote_url, publication.remote_status,
                            publication.details or {})

    def check(self, client) -> str:
        with self._browser() as page:
            if not self.recipe.get("new_post_url"):
                login(page, self.login_url, self.username, self.password)
                return "Signed in to the admin panel. Next: click Teach and show NEWSWIRE the post form."
            self._open_form(page)
            fields = self.recipe.get("fields") or {}
            found = [k for k in ("title", "body", "image", "excerpt", "tags", "category")
                     if k in fields and (fields[k].get("frame") or _resolve(page, fields[k]) is not None)]
            missing = [k for k in ("title", "body") if k not in found]
            if missing or _resolve(page, self.recipe.get("submit") or {}) is None:
                raise PublishError("Signed in, but the post form looks different now (missing: "
                                   + ", ".join(missing + ([] if _resolve(page, self.recipe.get("submit") or {}) else
                                                          ["publish button"])) + "). Click Teach again.",
                                   retryable=False)
            return "Signed in; the post form has " + ", ".join(found) + " and the publish button."


# --- teaching: a visible browser window and the overlay ----------------------------------------------------

TEACH_STEPS = [
    {"key": "page", "mode": "page", "message": "Go to the page where you write a NEW post (open “Add news” or similar), "
                                               "then press “This is the page”."},
    {"key": "title", "mode": "pick", "message": "Click the box where the HEADLINE (title) goes."},
    {"key": "body", "mode": "pick", "message": "Click the box where the ARTICLE TEXT goes."},
    {"key": "image", "mode": "pick", "optional": True,
     "message": "Click the IMAGE upload button or box. No image upload? Press Skip."},
    {"key": "excerpt", "mode": "pick", "optional": True,
     "message": "Click the SUMMARY (excerpt / short description) box. None? Press Skip."},
    {"key": "category", "mode": "pick", "optional": True, "message": "Click the CATEGORY box or list. None? Press Skip."},
    {"key": "tags", "mode": "pick", "optional": True, "message": "Click the TAGS box. None? Press Skip."},
    {"key": "submit", "mode": "pick",
     "message": "Click the PUBLISH (or Save / Submit) button. Don't worry: NEWSWIRE will not press it now."},
]

_sessions: dict[str, "TeachSession"] = {}
_sessions_guard = threading.Lock()


class TeachSession:
    """One teaching run in its own thread; the web app polls `status()`."""

    def __init__(self, dest: PublishingDestination, *, headless: bool = False,
                 driver: Callable[[Any, str], None] | None = None, session_factory=None):
        sec = load_secrets(dest)
        self.dest_id = dest.id
        self.login_url = dest.base_url or ""
        self.username = dest.username or ""
        self.password = sec.get("password") or ""
        self.storage_state = sec.get("storage_state")
        self.headless = headless
        self.driver = driver  # tests: performs each step instead of a person
        self.session_factory = session_factory
        self.state: dict[str, Any] = {"state": "starting", "mode": "idle", "step": 0, "total": len(TEACH_STEPS),
                                      "message": "Opening the browser window…", "error": None, "optional": False}
        self.answer: dict | None = None
        self.cancelled = False
        self.thread = threading.Thread(target=self._run, name=f"teach-{dest.id}", daemon=True)

    # bindings called from the page ------------------------------------------------------------------
    def _js_state(self, source) -> dict:
        return {k: self.state.get(k) for k in ("mode", "step", "total", "message", "optional")}

    def _js_pick(self, source, info) -> None:
        if self.state.get("mode") == "pick" and isinstance(info, dict):
            self.answer = {**info, "_frame": source.get("frame")}

    def _js_action(self, source, name) -> None:
        if name == "cancel":
            self.cancelled = True
        elif name == "page" and self.state.get("mode") == "page":
            self.answer = {"url": source["page"].url}
        elif name == "skip" and self.state.get("optional"):
            self.answer = {"skip": True}

    def status(self) -> dict:
        return {k: self.state.get(k) for k in ("state", "mode", "step", "total", "message", "error")}

    def _set(self, **values) -> None:
        self.state.update(values)

    @staticmethod
    def _push(page) -> None:
        """Show the new instruction at once instead of at the overlay's next poll."""
        try:
            page.evaluate("() => window.__nwRefresh && window.__nwRefresh()")
        except Exception:  # noqa: BLE001 - mid-navigation; the overlay polls anyway
            pass

    def _wait(self, page, deadline: float, until: Callable[[], bool]) -> None:
        while not until():
            if self.cancelled:
                raise _Cancelled()
            if page.is_closed():
                raise _Cancelled()
            if time.monotonic() > deadline:
                raise SetupError("Teaching took too long and was stopped. Click Teach to start again.")
            page.wait_for_timeout(250)

    def _run(self) -> None:
        lock = _lock(self.dest_id)
        if not lock.acquire(timeout=5):
            self._set(state="failed", mode="idle", error="The robot browser is busy publishing to this website. "
                                                         "Try Teach again in a minute.")
            return
        try:
            with _sync_playwright()() as p:
                browser = _launch(p, headless=self.headless)
                try:
                    self._teach(browser)
                finally:
                    browser.close()
        except _Cancelled:
            self._set(state="cancelled", mode="idle", message="Teaching was cancelled; nothing was changed.")
        except (SetupError, PublishError) as exc:
            self._set(state="failed", mode="idle", error=str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("teaching failed")
            self._set(state="failed", mode="idle", error=f"The setup browser stopped: {str(exc).splitlines()[0][:200]}")
        finally:
            lock.release()

    def _teach(self, browser) -> None:
        ctx = browser.new_context(storage_state=self.storage_state or None, viewport=None if not self.headless else
                                  {"width": 1280, "height": 900})
        ctx.expose_binding("__nwTeachState", self._js_state)
        ctx.expose_binding("__nwTeachPick", self._js_pick)
        ctx.expose_binding("__nwTeachAction", self._js_action)
        ctx.add_init_script(OVERLAY_JS)
        page = ctx.new_page()
        deadline = time.monotonic() + TEACH_SECONDS
        self._set(state="running", mode="login", message="Signing in to the admin panel…")
        try:
            login(page, self.login_url, self.username, self.password)
        except PublishError as exc:
            if "email and password" in str(exc):
                raise SetupError(str(exc)) from exc
            # A captcha or a two-step code: the person finishes signing in in this window.
            self._set(message="Finish signing in in this window (captcha or code), then wait a moment.")
            human_deadline = time.monotonic() + HUMAN_LOGIN_SECONDS
            self._wait(page, human_deadline, lambda: not needs_login(page) and _blocked_reason(page) is None)
        recipe: dict[str, Any] = {"fields": {}}
        for index, step in enumerate(TEACH_STEPS):
            self.answer = None
            self._set(mode=step["mode"], step=index, message=step["message"], optional=bool(step.get("optional")))
            self._push(page)
            if self.driver is not None:
                self.driver(page, step["key"])
            self._wait(page, deadline, lambda: self.answer is not None)
            answer = self.answer or {}
            if answer.get("skip"):
                continue
            if step["key"] == "page":
                recipe["new_post_url"] = answer["url"]
                continue
            frame = answer.pop("_frame", None)
            field = {k: answer.get(k) for k in ("selector", "tag", "type", "editable", "text")}
            if frame is not None and frame != page.main_frame:
                # A click inside an embedded editor (TinyMCE and similar): remember the frame itself.
                handle = frame.frame_element()
                field = {"selector": handle.evaluate("e => window.__nwCssPath(e)"), "tag": "iframe", "frame": True}
            if not field.get("selector"):
                raise SetupError("NEWSWIRE could not identify what you clicked. Click Teach and try again.")
            if step["key"] == "submit":
                recipe["submit"] = field
            else:
                recipe["fields"][step["key"]] = field
        self._set(mode="idle", message="Saving what you showed…")
        state = ctx.storage_state()
        self._save(recipe, state)
        self._set(state="done", mode="idle", message="Done. NEWSWIRE now knows this website's post form.")

    def _save(self, recipe: dict, storage_state: dict) -> None:
        from app.core.db import SessionLocal

        db = (self.session_factory or SessionLocal)()
        try:
            dest = db.get(PublishingDestination, self.dest_id)
            if dest is None:
                raise SetupError("The destination was deleted while teaching.")
            sec = load_secrets(dest)
            save_secrets(dest, {**sec, "storage_state": storage_state})
            set_config(dest, recipe=recipe, taught_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            dest.last_check_status = "OK: Post form taught (" + ", ".join(sorted(recipe["fields"])) + ")"
            from app.core.db import utcnow

            dest.last_checked_at = utcnow()
            db.commit()
        finally:
            db.close()


class _Cancelled(Exception):
    pass


def start_teaching(dest: PublishingDestination, **kwargs) -> TeachSession:
    with _sessions_guard:
        current = _sessions.get(str(dest.id))
        if current is not None and current.thread.is_alive():
            return current
        session = TeachSession(dest, **kwargs)
        _sessions[str(dest.id)] = session
    session.thread.start()
    return session


def teaching_status(dest_id) -> dict | None:
    session = _sessions.get(str(dest_id))
    return session.status() if session is not None else None


def cancel_teaching(dest_id) -> bool:
    session = _sessions.get(str(dest_id))
    if session is None or not session.thread.is_alive():
        return False
    session.cancelled = True
    return True


__all__ = [
    "RobotConnector", "SetupError", "TEACH_STEPS", "WordPressLoginConnector", "cancel_teaching", "detect_wordpress",
    "setup_website", "start_teaching", "teaching_status", "website_connector", "website_problem", "website_summary",
    "wordpress_setup",
]
