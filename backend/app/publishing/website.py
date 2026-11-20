"""Publish to any website through its own admin login: the login address, the admin's email or
username, and the password.

* **WordPress** is recognised behind the login. NEWSWIRE signs in once, creates an application
  password named "NEWSWIRE" for that account and publishes through the WordPress REST API (the
  same code as a WordPress destination). If the application password is later revoked, the stored
  login creates a new one.
* **Any other site** (a custom admin panel) is published by a robot browser: NEWSWIRE's own
  pure-Python engine (`app/robot`) drives the Chrome, Edge, Brave or Chromium already installed
  on the computer, signs in and fills in the site's own "new post" form. An administrator shows it
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

import html
import json
import logging
import re
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
from app.robot import (BindingCall, Browser, BrowserClosed, BrowserLaunchError, BrowserNotFound, Element, Page,
                       RobotError, RobotTimeout)

log = logging.getLogger(__name__)

OVERLAY_JS = (Path(__file__).with_name("teach_overlay.js")).read_text(encoding="utf-8")
# The overlay's "a selector that finds this element again", as a function Python can call too.
CSS_PATH_JS = ("function(el) {" + OVERLAY_JS.split("// <css-path>")[1].split("// </css-path>")[0]
               + " return cssPath(el); }")
NAV_SECONDS = 45.0
SETTLE_SECONDS = 20.0
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

PASSWORD_FIELD = "input[type=password]"
_TYPED = (":not([type=password]):not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=submit])"
          ":not([type=button]):not([type=file]):not([type=search])")
USER_FIELDS = ("input[type=email]", "input[autocomplete=username]", f"input[name*=user i]{_TYPED}",
               f"input[name*=email i]{_TYPED}", f"input[name*=login i]{_TYPED}", f"input[id*=user i]{_TYPED}",
               f"input[id*=email i]{_TYPED}", "input[type=text]", "input[type=tel]", "input:not([type])")
SUBMIT_FIELDS = ("button[type=submit]", "input[type=submit]", "button:not([type])", "[role=button]")
ERROR_TEXT = ("[role=alert]", ".alert-danger", ".alert-error", ".error", ".errors", ".invalid-feedback",
              ".text-danger", ".notice-error", ".help-block.error", ".field-error", ".is-invalid")
CAPTCHA = ("iframe[src*=recaptcha]", "iframe[src*=hcaptcha]", "iframe[src*=turnstile]", ".g-recaptcha", ".h-captcha",
           ".cf-turnstile")
TWO_STEP = ("input[autocomplete=one-time-code]", "input[name*=otp i]", "input[name*=2fa i]",
            "input[name*=token i][inputmode=numeric]")
SCREEN_NEEDED = ("Teaching needs NEWSWIRE running on a computer with a screen (it opens a browser window). Run it "
                 "on your Mac or PC for this one-time step.")


def open_browser(*, headless: bool, storage_state: dict | None = None, **kwargs) -> Browser:
    """Start the robot browser, turning "cannot start" into words an administrator can act on."""
    try:
        return Browser.launch(headless=headless, executable=get_settings().browser_path or None,
                              storage_state=storage_state, **kwargs)
    except BrowserNotFound as exc:
        raise PublishError(str(exc), retryable=False) from exc
    except BrowserLaunchError as exc:
        if not headless and "no screen" in str(exc):
            raise PublishError(SCREEN_NEEDED, retryable=False) from exc
        raise PublishError(f"The robot browser did not start. {exc}") from exc


def _quiet(fn: Callable[[], Any], default: Any = None) -> Any:
    """Looking at a page that is busy changing: a failed look is "not there". A closed browser is
    still an error."""
    try:
        return fn()
    except BrowserClosed:
        raise
    except RobotError:
        return default


def _seen(page: Page, *selectors: str) -> bool:
    return _quiet(lambda: page.find(*selectors) is not None, False)


def needs_login(page: Page) -> bool:
    return _seen(page, PASSWORD_FIELD)


def _blocked_reason(page: Page) -> str | None:
    if _seen(page, *CAPTCHA):
        return "captcha"
    if _seen(page, *TWO_STEP):
        return "two-step"
    return None


def _error_text(page: Page, limit: int = 250) -> str:
    """What the page says went wrong (the first visible error message with words in it)."""
    for selector in ERROR_TEXT:
        text = _quiet(lambda sel=selector: (lambda el: el.inner_text() if el is not None else "")(page.find(sel)), "")
        if text:
            return " ".join(text.split())[:limit]
    return ""


def login(page: Page, login_url: str, username: str, password: str) -> None:
    """Sign in on the login page. Raises PublishError with a plain explanation when it cannot."""
    page.goto(login_url, timeout=NAV_SECONDS)
    page.settle(8)
    pw = page.find(PASSWORD_FIELD)
    if pw is None:
        return  # the stored session is still signed in
    form = pw.closest("form")
    # A username/email box shown on the same form as the password (not a search box elsewhere).
    user = page.find(*USER_FIELDS, root=form)
    if user is not None:
        user.fill(username)
    pw.fill(password)
    submit = page.find(*SUBMIT_FIELDS, root=form)
    if submit is not None:
        submit.click()
    else:
        pw.press("Enter")
    page.settle(10, quiet=1.0)
    if needs_login(page):
        if _blocked_reason(page) == "captcha":
            raise PublishError("The website's login asks for a captcha. Click Teach in NEWSWIRE and complete it in "
                               "the window once; NEWSWIRE then keeps that session.", retryable=False)
        detail = _error_text(page, 200)
        raise PublishError("The website did not accept the admin email and password"
                           + (f": {detail}" if detail else ". Check them under Edit."), retryable=False)
    if _blocked_reason(page) == "two-step":
        raise PublishError("The website asks for a two-step code. Click Teach in NEWSWIRE and enter the code in the "
                           "window once; NEWSWIRE then keeps that session.", retryable=False)


def _resolve(page: Page, field: dict) -> Element | None:
    """The element for a taught field. Falls back to the visible text of a taught button."""
    selector = field.get("selector") or ""
    if selector:
        el = page.find(selector)
        if el is not None:
            return el
    text = (field.get("text") or "").strip()
    if text and field.get("tag") in ("button", "a", "input"):
        el = page.find_button(text) or page.find_text(text)
        if el is not None:
            return el
    if selector:
        return page.find(selector, visible=False)  # present but hidden (a styled file input)
    return None


def article_text(html_body: str) -> str:
    soup = BeautifulSoup(html_body, "html.parser")
    blocks = [b.get_text(" ", strip=True) for b in soup.find_all(["p", "h2", "h3", "h4", "li", "blockquote"])]
    text = "\n\n".join(b for b in blocks if b)
    return text or soup.get_text("\n", strip=True)


def _text_as_html(text: str) -> str:
    return "".join(f"<p>{html.escape(block).replace(chr(10), '<br>')}</p>" for block in text.split("\n\n") if block)


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


def _type_into(el: Element, value: str) -> None:
    """Text boxes and rich-text boxes are typed into; a list gets the option with that name (and
    keeps the site's default when it has none)."""
    el.fill(value)


def _fill_body(page: Page, field: dict, html_body: str, fmt: str) -> None:
    text = article_text(html_body)
    rich = html_body if fmt != "text" else _text_as_html(text)
    el = _resolve(page, field)
    if el is None:
        raise PublishError("The article text box was not found on the post form. The site may have changed: click "
                           "Teach again.", retryable=False)
    kind = el.kind()
    if field.get("frame") or kind == "iframe":
        # An editor that lives in its own frame (TinyMCE and similar).
        frame = el.content_frame()
        body = None
        if frame is not None:
            page.wait_for(lambda: frame.body() is not None, 10)
            body = frame.body()
        if body is None:
            raise PublishError("The article text box was not found (it was inside a frame). Click Teach again.",
                               retryable=False)
        body.call(PASTE_JS, {"html": rich, "text": text})
        return
    if kind == "editable":
        el.call(PASTE_JS, {"html": rich, "text": text})
        return
    if kind == "textarea" and fmt != "text" and el.call(RICH_TEXTAREA_JS, html_body):
        return
    el.fill(html_body if fmt == "html" else text)


def _attach_image(page: Page, field: dict, path: Path) -> None:
    el = _resolve(page, field)
    if el is None:
        raise PublishError("The image upload on the post form was not found. Click Teach again, or Skip the image "
                           "step.", retryable=False)
    if el.kind() == "input:file":
        el.set_files(path)
    else:
        try:
            page.choose_files(el, path)
        except RobotTimeout as exc:
            raise PublishError("The image upload on the post form did not open a file window when pressed. Click "
                               "Teach again and click the upload box itself, or Skip the image step.",
                               retryable=False) from exc
    page.settle(15)


def _screenshot(page: Page, dest_id) -> str | None:
    try:
        folder = get_settings().logs_dir / "robot"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{dest_id}-{time.strftime('%Y%m%d-%H%M%S')}.png"
        page.screenshot(path, full_page=True)
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
    def _browser(self) -> Iterator[Page]:
        lock = _lock(self.dest.id)
        if not lock.acquire(timeout=10 * 60):
            raise PublishError("The robot browser is busy with this website (teaching or another post); "
                               "NEWSWIRE will try again.")
        try:
            browser = open_browser(headless=self.headless, storage_state=load_secrets(self.dest).get("storage_state"))
            try:
                yield browser.page
                sec = load_secrets(self.dest)
                save_secrets(self.dest, {**sec, "storage_state": browser.storage_state()})
            finally:
                browser.close()
        except BrowserClosed as exc:
            raise PublishError("The robot browser closed before it had finished; NEWSWIRE will try again.") from exc
        except RobotError as exc:
            raise PublishError(f"The robot browser could not finish: {exc}") from exc
        finally:
            lock.release()

    def _open_form(self, page: Page) -> None:
        url = self.recipe.get("new_post_url")
        if not url:
            raise PublishError("Show NEWSWIRE the website's post form first: Publishing → Destinations → Teach.",
                               retryable=False)
        page.goto(url, timeout=NAV_SECONDS)
        page.settle(SETTLE_SECONDS)
        if needs_login(page) or urlsplit(page.url).path.rstrip("/") == urlsplit(self.login_url).path.rstrip("/"):
            login(page, self.login_url, self.username, self.password)
            page.goto(url, timeout=NAV_SECONDS)
            page.settle(SETTLE_SECONDS)
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
                return self._fill_and_submit(page, article, fields, html_body, img_path)
            except PublishError as exc:
                shot = _screenshot(page, self.dest.id)
                raise PublishError(f"{exc}" + (f" (screenshot: storage/{shot})" if shot else ""), exc.retryable) from exc
            except BrowserClosed:
                raise
            except Exception as exc:  # noqa: BLE001 - a page that would not load, a form that changed mid-way
                shot = _screenshot(page, self.dest.id)
                raise PublishError(f"The robot browser could not finish: {str(exc).splitlines()[0][:200]}"
                                   + (f" (screenshot: storage/{shot})" if shot else "")) from exc

    def _fill_and_submit(self, page: Page, article, fields: dict, html_body: str, img_path: Path | None) -> RemoteResult:
        self._open_form(page)
        title = _resolve(page, fields["title"])
        if title is None:
            raise PublishError("The title box was not found on the post form. The site may have changed: "
                               "click Teach again.", retryable=False)
        _type_into(title, article.headline)
        _fill_body(page, fields["body"], html_body, self.body_format)
        if fields.get("excerpt") and (article.excerpt or article.seo_description):
            el = _resolve(page, fields["excerpt"])
            if el is not None:
                _type_into(el, article.excerpt or article.seo_description or "")
        if fields.get("tags") and article.tags:
            el = _resolve(page, fields["tags"])
            if el is not None:
                _type_into(el, ", ".join(t.name for t in article.tags))
        if fields.get("category") and article.category:
            el = _resolve(page, fields["category"])
            if el is not None:
                _type_into(el, article.category.name)
        if fields.get("image") and img_path is not None and img_path.is_file():
            _attach_image(page, fields["image"], img_path)
        submit = _resolve(page, self.recipe["submit"])
        if submit is None:
            raise PublishError("The Publish button was not found on the post form. Click Teach again.",
                               retryable=False)
        before = page.url
        submit.click(timeout=30)  # a button that is greyed out while the photo uploads is waited for
        # A classic form loads a new page; a single-page admin panel saves in the background and
        # stays on one address. Either way: wait until the page has finished and gone quiet.
        page.settle(SETTLE_SECONDS, quiet=1.5)
        if needs_login(page):
            raise PublishError("The website signed NEWSWIRE out while posting; it will try again.")
        if page.url == before:
            detail = _error_text(page)
            if detail:
                raise PublishError(f"The website did not accept the post: {detail}", retryable=False)
        link = self._find_link(page, article.headline)
        return RemoteResult(link or page.url, link or page.url, "published", {"landed_on": page.url, "robot": True})

    @staticmethod
    def _find_link(page: Page, headline: str) -> str | None:
        """The public link of the new post, when the page after Publish shows one."""
        looks = (lambda: page.find_link(headline[:80]),
                 lambda: page.find_link("", pattern=r"^(view|see|open|preview)\b"))
        for look in looks:
            href = _quiet(lambda look=look: (lambda el: el.attribute("href") if el is not None else None)(look()))
            if href and not href.startswith(("#", "javascript:")):
                return urljoin(page.url, href)
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
                     if k in fields and _resolve(page, fields[k]) is not None]
            missing = [k for k in ("title", "body") if k not in found]
            if _resolve(page, self.recipe.get("submit") or {}) is None:
                missing.append("publish button")
            if missing:
                raise PublishError("Signed in, but the post form looks different now (missing: " + ", ".join(missing)
                                   + "). Click Teach again.", retryable=False)
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
WINDOW_CLOSED = "The setup window was closed, so teaching stopped; nothing was changed. Click Teach to start again."

_sessions: dict[str, "TeachSession"] = {}
_sessions_guard = threading.Lock()


class TeachSession:
    """One teaching run in its own thread; the web app polls `status()`."""

    def __init__(self, dest: PublishingDestination, *, headless: bool = False,
                 driver: Callable[[Page, str], None] | None = None, session_factory=None):
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
        self.browser: Browser | None = None
        self.thread = threading.Thread(target=self._run, name=f"teach-{dest.id}", daemon=True)

    # called by the overlay in the page (on the browser connection's thread: no waiting here) --------
    def _overlay_state(self) -> dict:
        return {k: self.state.get(k) for k in ("mode", "step", "total", "message", "optional")}

    def _from_overlay(self, call: BindingCall) -> None:
        try:
            msg = json.loads(call.payload)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        current = msg.get("step") == self.state.get("step") and self.answer is None
        if msg.get("type") == "pick" and current and self.state.get("mode") == "pick":
            info = msg.get("info")
            if isinstance(info, dict):
                self.answer = {**info, "_frame": call.frame_id, "_page": call.page}
        elif msg.get("type") == "action":
            name = msg.get("name")
            if name == "cancel":
                self.cancelled = True
            elif name == "page" and current and self.state.get("mode") == "page":
                self.answer = {"url": call.page.url, "_page": call.page}
            elif name == "skip" and current and self.state.get("optional"):
                self.answer = {"skip": True}
        call.reply(self._overlay_call())

    def _overlay_call(self) -> str:
        return "globalThis.__nwTeachSet && globalThis.__nwTeachSet(" + json.dumps(self._overlay_state()) + ")"

    def status(self) -> dict:
        return {k: self.state.get(k) for k in ("state", "mode", "step", "total", "message", "error")}

    def _set(self, **values) -> None:
        self.state.update(values)
        self._push()

    def _push(self) -> None:
        """Show the new instruction at once instead of at the overlay's next poll."""
        browser = self.browser
        if browser is None:
            return
        for page in browser.pages:
            page.run_in_world(self._overlay_call())

    def _wait(self, deadline: float, until: Callable[[], bool]) -> None:
        browser = self.browser
        while True:
            if self.cancelled:
                raise _Cancelled()
            if not browser.alive:
                raise BrowserClosed(WINDOW_CLOSED)
            if _quiet(until, False):
                return
            if time.monotonic() > deadline:
                raise SetupError("Teaching took too long and was stopped. Click Teach to start again.")
            time.sleep(0.25)

    def _run(self) -> None:
        lock = _lock(self.dest_id)
        if not lock.acquire(timeout=15):
            self.state.update(state="failed", mode="idle", error="The robot browser is busy publishing to this website. "
                                                                 "Try Teach again in a minute.")
            return
        cancelled = {"state": "cancelled", "mode": "idle", "message": "Teaching was cancelled; nothing was changed."}
        try:
            self.browser = open_browser(headless=self.headless, storage_state=self.storage_state,
                                        scripts=[(OVERLAY_JS, True)], bindings={"__nwTeach": (self._from_overlay, True)})
            self._teach(self.browser)
            outcome = {"state": "done", "mode": "idle", "message": "Done. NEWSWIRE now knows this website's post form."}
        except _Cancelled:
            outcome = cancelled
        except BrowserClosed:
            # The person closed the window (or the browser quit): that is a way of cancelling.
            outcome = cancelled if self.cancelled else {**cancelled, "message": WINDOW_CLOSED}
        except (SetupError, PublishError) as exc:
            outcome = {"state": "failed", "mode": "idle", "error": str(exc)}
        except RobotError as exc:
            log.warning("teaching failed: %s", exc)
            outcome = {"state": "failed", "mode": "idle", "error": f"The setup browser stopped: {str(exc)[:300]}"}
        except Exception as exc:  # noqa: BLE001
            log.exception("teaching failed")
            outcome = {"state": "failed", "mode": "idle",
                       "error": f"The setup browser stopped: {str(exc).splitlines()[0][:200]}"}
        finally:
            browser, self.browser = self.browser, None
            try:
                if browser is not None:
                    browser.close()
            except Exception:  # noqa: BLE001
                log.exception("closing the setup browser failed")
            lock.release()
        # Only now is this run over: the window is gone and the website is free for the next one.
        self.state.update(outcome)

    def _teach(self, browser: Browser) -> None:
        page = browser.page
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
            self._wait(human_deadline, lambda: not any(needs_login(p) or _blocked_reason(p) for p in browser.pages))
        recipe: dict[str, Any] = {"fields": {}}
        for index, step in enumerate(TEACH_STEPS):
            self.answer = None
            self._set(mode=step["mode"], step=index, message=step["message"], optional=bool(step.get("optional")))
            if self.driver is not None:
                self.driver(browser.page, step["key"])
            self._wait(deadline, lambda: self.answer is not None)
            answer = dict(self.answer or {})
            if answer.get("skip"):
                continue
            if step["key"] == "page":
                recipe["new_post_url"] = answer["url"]
                continue
            frame_id, origin = answer.pop("_frame", None), answer.pop("_page", None)
            field = {k: answer.get(k) for k in ("selector", "tag", "type", "editable", "text")}
            if origin is not None and frame_id and frame_id != origin.main_frame_id:
                # A click inside an embedded editor (TinyMCE and similar): remember the frame itself.
                owner = _quiet(lambda: origin.frame_owner(frame_id))
                selector = _quiet(lambda: owner.call(CSS_PATH_JS), "") if owner is not None else ""
                field = {"selector": selector, "tag": "iframe", "frame": True}
            if not field.get("selector"):
                raise SetupError("NEWSWIRE could not identify what you clicked. Click Teach and try again.")
            if step["key"] == "submit":
                recipe["submit"] = field
            else:
                recipe["fields"][step["key"]] = field
        self._set(mode="idle", message="Saving what you showed…")
        state = browser.storage_state()
        self._save(recipe, state)

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
        if current is not None and current.thread.is_alive() and current.state.get("state") in ("starting", "running"):
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
    "open_browser", "setup_website", "start_teaching", "teaching_status", "website_connector", "website_problem",
    "website_summary", "wordpress_setup",
]
