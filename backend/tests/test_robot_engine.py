"""The robot browser engine (app/robot): a real browser from this computer drives pages served
on 127.0.0.1. Skipped when no Chromium-family browser is installed."""

from __future__ import annotations

import os
import signal
import threading
import time
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from app import robot
from app.robot import (Browser, BrowserClosed, BrowserNotFound, NavigationError, RobotError, RobotTimeout, ScriptError,
                       Stale)
from app.robot.cdp import WebSocketTransport


def _browser_or_none():
    try:
        return robot.find_browser(os.environ.get("NEWSWIRE_BROWSER_PATH") or None, headless=True)
    except BrowserNotFound:
        return None


BROWSER = _browser_or_none()
pytestmark = pytest.mark.skipif(BROWSER is None, reason="No Chromium-family browser is installed")

PAGES = {
    "/": """<!doctype html><title>Home</title><h1>Home</h1>
<a href="/form">Open the form</a> <a href="/form" id="hidden-link" style="display:none">Hidden</a>
<a href="/form"><img alt="Picture link" src="data:image/gif;base64,R0lGODlhAQABAAAAACw="></a>""",
    "/form": """<!doctype html><title>Form</title>
<form method="post" action="/echo" id="f">
  <input name="title" id="title" placeholder="Title">
  <input name="shadow" id="shadow" value="was here">
  <input name="when" id="when" type="date">
  <input name="count" id="count" type="number">
  <input name="locked" id="locked" readonly value="old">
  <input name="short" id="short" maxlength="5">
  <textarea name="story" id="story">old text</textarea>
  <div id="rich" contenteditable="true"><p>old</p></div>
  <select name="section" id="section"><option value="">Choose</option><option value="7">National</option>
    <option value="9">Sports  desk</option></select>
  <input type="hidden" name="rich" id="rich-value">
  <input type="checkbox" id="check">
  <span id="plain">plain</span>
  <button type="submit" id="send">Save story</button>
</form>
<p id="log"></p>
<script>
  // A "framework": it only believes what input events tell it.
  const seen = {};
  for (const el of document.querySelectorAll('input,textarea,select')) {
    el.addEventListener('input', () => { seen[el.id] = el.value; document.getElementById('log').textContent = JSON.stringify(seen); });
  }
  document.getElementById('f').addEventListener('submit', () => {
    document.getElementById('rich-value').value = document.getElementById('rich').innerHTML;
  });
</script>""",
    "/enter": """<!doctype html><title>Enter</title>
<form method="post" action="/echo"><input name="q" id="q"><input type="password" name="p" id="p">
<button style="display:none">Go</button></form>""",
    "/covered": """<!doctype html><title>Covered</title>
<button id="under" onclick="document.title='under pressed'">Under</button>
<div style="position:fixed;inset:0;background:rgba(0,0,0,.4)" id="veil"></div>
<button id="late" disabled onclick="document.title='late pressed'" style="position:relative;z-index:5">Late</button>
<button id="never" disabled style="position:relative;z-index:5">Never</button>
<script>setTimeout(() => { document.getElementById('late').disabled = false; }, 600);</script>""",
    "/far": """<!doctype html><title>Far</title><div style="height:4000px">tall</div>
<header style="position:fixed;top:0;left:0;right:0;height:60px;background:#ccc">sticky</header>
<button id="deep" onclick="document.title='deep pressed'">Deep</button><div style="height:2000px"></div>""",
    "/upload": """<!doctype html><title>Upload</title>
<form method="post" action="/files" enctype="multipart/form-data">
  <input type="file" name="direct" id="direct" style="display:none">
  <input type="file" name="chosen" id="chosen" style="display:none">
  <button type="button" id="pick" onclick="document.getElementById('chosen').click()">Choose a photo</button>
  <button type="button" id="dud">Not an upload</button>
  <button type="submit" id="go">Upload</button>
</form>""",
    "/frames": """<!doctype html><title>Frames</title>
<iframe id="same" name="editor_frame" src="/inner" style="width:400px;height:120px"></iframe>
<iframe id="other" src="{other}/inner" style="width:400px;height:120px"></iframe>""",
    "/inner": """<!doctype html><title>Inner</title><body contenteditable="true"><p>inside</p></body>""",
    "/dialogs": """<!doctype html><title>Dialogs</title>
<button id="ask" onclick="document.title = confirm('Publish now?') ? 'yes' : 'no'">Ask</button>
<button id="tell" onclick="alert('Saved!'); document.title='told'">Tell</button>""",
    "/storage": """<!doctype html><title>Storage</title><p id="out"></p>
<script>document.getElementById('out').textContent = (localStorage.getItem('token') || 'none') + '|' + document.cookie;</script>""",
    "/slow-xhr": """<!doctype html><title>XHR</title><p id="out">waiting</p>
<button id="load" onclick="fetch('/slow').then(r => r.text()).then(t => { document.getElementById('out').textContent = t; })">Load</button>""",
    "/spa": """<!doctype html><title>SPA</title>
<button id="route" onclick="history.pushState({}, '', '/spa/posts/9')">Go</button>
<button id="popup" onclick="window.open('/form', '_blank')">New tab</button>""",
    "/throws": """<!doctype html><title>Throws</title>""",
}


class Site:
    def __init__(self):
        self.posts: list[dict] = []
        self.files: list[dict] = []
        site = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def handle(self):
                try:
                    super().handle()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the browser stopped waiting (the timeout test)

            def _send(self, code, body="", headers=None):
                data = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # noqa: N802
                path = urlsplit(self.path).path
                if path == "/slow":
                    time.sleep(1.2)
                    return self._send(200, "loaded")
                if path == "/hang":
                    time.sleep(8)
                    return self._send(200, "late")
                if path == "/login":
                    return self._send(303, headers={
                        "Location": "/storage",
                        "Set-Cookie": "sid=abc123; Path=/; HttpOnly"})
                if path == "/remember":
                    return self._send(200, "<script>localStorage.setItem('token', 'tok-9');"
                                           "document.cookie = 'pref=dark; path=/; max-age=86400';</script>ok")
                if path == "/download":
                    return self._send(200, "data", {"Content-Disposition": "attachment; filename=x.bin"})
                if path.startswith("/spa/"):
                    path = "/spa"
                if path in PAGES:
                    return self._send(200, PAGES[path].replace("{other}", site.other))
                return self._send(404, "<title>Missing</title>not found")

            def do_POST(self):  # noqa: N802
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                path = urlsplit(self.path).path
                if path == "/echo":
                    site.posts.append({k: v[0] for k, v in parse_qs(raw.decode(), keep_blank_values=True).items()})
                    return self._send(303, headers={"Location": "/saved"})
                if path == "/files":
                    msg = BytesParser(policy=email_policy).parsebytes(
                        f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode() + raw)
                    got = {}
                    for part in msg.iter_parts():
                        name = part.get_param("name", header="content-disposition")
                        got[name] = (part.get_filename(), len(part.get_payload(decode=True) or b""))
                    site.files.append(got)
                    return self._send(200, "<title>Uploaded</title>ok")
                return self._send(404, "no")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        port = self.server.server_port
        self.base = f"http://127.0.0.1:{port}"
        self.other = f"http://localhost:{port}"  # another site as far as the browser is concerned
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


PAGES["/saved"] = "<!doctype html><title>Saved</title><p class='ok'>Saved.</p>"


@pytest.fixture(scope="module")
def site():
    s = Site()
    yield s
    s.close()


@pytest.fixture(scope="module")
def browser():
    with Browser.launch(headless=True, executable=BROWSER) as b:
        yield b


@pytest.fixture()
def page(browser, site):
    p = browser.page
    p.dialogs.clear()
    site.posts.clear()
    site.files.clear()
    return p


def title(page) -> str:
    return page.evaluate("function() { return document.title; }")


# --- finding ------------------------------------------------------------------------------------------


def test_self_check_reports_the_browser():
    result = robot.self_check(str(BROWSER))
    assert result["ok"] is True, result
    assert str(BROWSER) in result["message"] and result["version"]


def test_goto_and_find(page, site):
    page.goto(f"{site.base}/")
    assert page.url == f"{site.base}/" and title(page) == "Home"
    assert page.find("h1").inner_text() == "Home"
    assert page.find("#nope") is None
    assert page.find("#hidden-link") is None                      # hidden things are skipped
    assert page.find("#hidden-link", visible=False) is not None   # unless asked for
    assert page.find("#hidden-link", hidden_ok=True).attribute("id") == "hidden-link"
    assert page.find("#nope", "h1", "a").kind() == "h1"           # selectors are preferences, in order
    assert page.find("a[[!!") is None                            # a broken selector matches nothing
    assert page.count("a") == 3 and page.count("#nope") == 0
    assert page.find_link("open the FORM").attribute("href") == "/form"
    assert page.find_link("picture").attribute("href") == "/form"  # named by the image inside it
    assert page.find_link("", pattern=r"^(view|open)\b") is not None
    assert page.find_link("no such link") is None
    assert page.find_text("Home").kind() == "h1"
    assert page.find_text("home") is None                          # exact text


def test_click_follows_a_link_and_old_elements_go_stale(page, site):
    page.goto(f"{site.base}/")
    heading = page.find("h1")
    before = page.url
    page.find_link("Open the form").click()
    assert page.wait_for_url_change(before, 10)
    assert page.settle(10)
    assert page.url.endswith("/form") and title(page) == "Form"
    with pytest.raises(Stale):
        heading.inner_text()


# --- typing -------------------------------------------------------------------------------------------


def test_fill_every_kind_of_field_and_submit(page, site):
    page.goto(f"{site.base}/form")
    page.find("#title").fill("গার্মেন্টস কারখানা খুলেছে — “Factories reopen”")
    page.find("#shadow").fill("replaced")
    page.find("#when").fill("2026-11-20")
    page.find("#count").fill("42")
    page.find("#locked").fill("new")            # read-only to a person; still set for the form
    page.find("#short").fill("123456789")       # the page's own limit applies, as when typing
    page.find("#story").fill("line one\nline two\n<b>not markup</b>")
    page.find("#rich").fill("Rich text here")
    page.find("#section").fill("Sports desk")   # a list: chosen by what the person sees
    assert page.find("#section").select_option("No such section") is False
    seen = page.find("#log").inner_text()
    assert '"title":"গার্মেন্টস' in seen and '"shadow":"replaced"' in seen and '"section":"9"' in seen
    assert page.find("#rich").inner_text() == "Rich text here"
    with pytest.raises(RobotError, match="cannot be typed"):
        page.find("#check").fill("x")
    with pytest.raises(RobotError, match="cannot be typed"):
        page.find("#plain").fill("x")
    before = page.url
    page.find_button("save story").click()
    assert page.wait_for_url_change(before, 10) and page.settle(10)
    assert title(page) == "Saved"
    post = site.posts[0]
    assert post["title"] == "গার্মেন্টস কারখানা খুলেছে — “Factories reopen”"
    assert post["shadow"] == "replaced" and post["when"] == "2026-11-20" and post["count"] == "42"
    assert post["locked"] == "new" and post["short"] == "12345" and post["section"] == "9"
    assert post["story"].replace("\r\n", "\n") == "line one\nline two\n<b>not markup</b>"
    assert "Rich text here" in post["rich"] and "old" not in post["rich"]


def test_fill_clears_and_handles_long_text(page, site):
    page.goto(f"{site.base}/form")
    box = page.find("#story")
    box.fill("")
    assert box.value() == ""
    long_text = ("সংবাদ " * 20 + "news line\n") * 700   # about 90 KB
    box.fill(long_text)
    assert box.value().replace("\r\n", "\n") == long_text
    page.find("#shadow").fill("")
    assert page.find("#shadow").value() == ""


def test_enter_submits_a_form(page, site):
    page.goto(f"{site.base}/enter")
    page.find("#q").fill("search me")
    before = page.url
    page.find("#p").press("Enter")
    assert page.wait_for_url_change(before, 10)
    assert site.posts == [{"q": "search me", "p": ""}]
    with pytest.raises(ValueError):
        page.press("NoSuchKey")


# --- clicking -----------------------------------------------------------------------------------------


def test_click_waits_for_a_disabled_button_and_presses_covered_ones(page, site):
    page.goto(f"{site.base}/covered")
    page.find("#late").click()                 # enabled after 0.6 s
    assert title(page) == "late pressed"
    page.find("#under").click(timeout=0.5)     # under a veil: pressed by script instead of failing
    assert title(page) == "under pressed"
    with pytest.raises(RobotError, match="disabled"):
        page.find("#never").click(timeout=0.5)


def test_click_scrolls_to_far_away_elements(page, site):
    page.goto(f"{site.base}/far")
    page.find("#deep").click()
    assert title(page) == "deep pressed"


# --- files --------------------------------------------------------------------------------------------


def test_files_go_into_hidden_inputs_and_through_file_dialogs(page, site, tmp_path):
    a = tmp_path / "ছবি one.png"
    a.write_bytes(b"\x89PNG" + b"a" * 2000)
    b = tmp_path / "two.jpg"
    b.write_bytes(b"\xff\xd8" + b"b" * 3000)
    page.goto(f"{site.base}/upload")
    page.find("#direct", visible=False).set_files(a)
    page.choose_files(page.find("#pick"), b)
    with pytest.raises(RobotTimeout, match="file dialog"):
        page.choose_files(page.find("#dud"), b, timeout=1)
    page.find("#go").click()
    assert page.wait_for(lambda: title(page) == "Uploaded", 10)
    assert site.files == [{"direct": ("ছবি one.png", 2004), "chosen": ("two.jpg", 3002)}]


# --- frames -------------------------------------------------------------------------------------------


def test_frames_same_site_and_other_site(page, site):
    page.goto(f"{site.base}/frames")
    page.settle(10)
    for frame_id in ("#same", "#other"):
        owner = page.find(frame_id)
        assert owner.kind() == "iframe"
        frame = owner.content_frame()
        assert frame is not None and not frame.is_main
        assert page.wait_for(lambda: frame.body() is not None, 10)
        body = frame.body()
        assert body.inner_text() == "inside"
        body.fill(f"typed into {frame_id}")
        assert frame.evaluate("function() { return document.body.innerText.trim(); }") == f"typed into {frame_id}"
        assert frame.evaluate("function() { return location.pathname; }") == "/inner"
        again = page.frame_owner(frame.frame_id)
        assert again is not None and again.attribute("id") == frame_id[1:]
    assert page.find("h1").content_frame() is None if page.find("h1") else True
    assert page.main_frame.is_main


# --- waiting ------------------------------------------------------------------------------------------


def test_settle_waits_for_background_requests(page, site):
    page.goto(f"{site.base}/slow-xhr")
    page.settle(5)
    page.find("#load").click()
    t0 = time.monotonic()
    assert page.settle(10) is True
    assert time.monotonic() - t0 > 1.0
    assert page.find("#out").inner_text() == "loaded"
    assert page.wait_for(lambda: False, timeout=0.3) is False


def test_same_page_navigation_and_new_tabs(browser, page, site):
    page.goto(f"{site.base}/spa")
    before = page.url
    page.find("#route").click()
    assert page.wait_for_url_change(before, 5) and page.url.endswith("/spa/posts/9")
    assert page.wait_for_url_change(page.url, 0.3) is False
    page.goto(f"{site.base}/spa#top")          # only the fragment changes: no new document
    page.goto(f"{site.base}/spa")
    page.find("#popup").click()
    assert page.wait_for(lambda: len(browser.pages) == 2, 10)
    popup = browser.pages[1]
    assert popup.wait_for(lambda: popup.url.endswith("/form") and popup.find("#title") is not None, 10)
    popup.find("#title").fill("in the new tab")
    assert popup.find("#title").value() == "in the new tab"
    browser._conn.send("Target.closeTarget", {"targetId": popup.target_id})
    assert page.wait_for(lambda: len(browser.pages) == 1, 10)
    assert popup.closed and not page.closed
    with pytest.raises(BrowserClosed):
        popup.find("#title")


# --- message boxes, errors ----------------------------------------------------------------------------


def test_message_boxes_are_answered_without_a_window(page, site):
    page.goto(f"{site.base}/dialogs")
    page.find("#ask").click()
    assert page.wait_for(lambda: title(page) == "yes", 5)
    page.find("#tell").click()
    assert page.wait_for(lambda: title(page) == "told", 5)
    assert page.dialogs == ["Publish now?", "Saved!"]


def test_addresses_that_cannot_be_opened(page, site):
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed_port = s.getsockname()[1]
    with pytest.raises(NavigationError, match="refused"):
        page.goto(f"http://127.0.0.1:{closed_port}/")
    with pytest.raises(NavigationError, match="does not exist"):
        page.goto("http://no-such-host.invalid/")
    with pytest.raises(NavigationError, match="download"):
        page.goto(f"{site.base}/download")
    with pytest.raises(RobotTimeout):
        page.goto(f"{site.base}/hang", timeout=1)
    page.goto(f"{site.base}/missing")            # a 404 page is still a page
    assert title(page) == "Missing"


def test_script_errors_are_reported(page, site):
    page.goto(f"{site.base}/throws")
    with pytest.raises(ScriptError, match="boom"):
        page.evaluate("function() { throw new Error('boom'); }")
    assert page.evaluate("async function(a, b) { return a + b; }", 2, 3) == 5
    assert page.evaluate("function(o) { return o.list.length + o.text; }", {"list": [1, 2], "text": "ক"}) == "2ক"


def test_screenshot(page, site, tmp_path):
    page.goto(f"{site.base}/far")
    shot = tmp_path / "shot.png"
    page.screenshot(shot)
    data = shot.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 2000
    height = int.from_bytes(data[20:24], "big")
    assert height > 3000          # the whole page, not just the window


# --- sign-in state, bindings, both transports ---------------------------------------------------------


@pytest.mark.parametrize("transport", ["pipe", "websocket"] if os.name == "posix" else ["websocket"])
def test_sign_in_state_survives_between_browsers(site, transport):
    with Browser.launch(headless=True, executable=BROWSER, transport=transport) as first:
        p = first.page
        p.goto(f"{site.base}/login")             # a session cookie (no expiry), HttpOnly
        p.goto(f"{site.base}/remember")          # localStorage and a lasting cookie
        state = first.storage_state()
    names = {c["name"]: c for c in state["cookies"]}
    assert names["sid"]["httpOnly"] is True and names["sid"]["expires"] == -1
    assert names["pref"]["expires"] > time.time()
    assert state["origins"] == [{"origin": site.base, "localStorage": [{"name": "token", "value": "tok-9"}]}]

    with Browser.launch(headless=True, executable=BROWSER, transport=transport, storage_state=state) as second:
        p = second.page
        p.goto(f"{site.base}/storage")
        assert p.find("#out").inner_text() == "tok-9|pref=dark"      # sid is HttpOnly: sent, not shown to scripts
        p.evaluate("function() { localStorage.clear(); }")
        p.goto(f"{site.base}/storage")
        assert p.find("#out").inner_text().startswith("none|")       # restored once, not forced back
        assert second.storage_state()["origins"] == []
        again = {c["name"] for c in second.storage_state()["cookies"]}
        assert {"sid", "pref"} <= again

    # Leftovers from other tools and broken entries are ignored, not fatal.
    odd = {"cookies": [{"name": "old", "value": "1", "domain": "127.0.0.1", "path": "/", "expires": 5},
                       {"name": "", "domain": "x"}, "junk", {"name": "ok", "value": "2", "domain": "127.0.0.1",
                                                              "path": "/", "expires": -1, "sameSite": "Lax"}],
           "origins": [{"origin": site.base}, "junk"]}
    with Browser.launch(headless=True, executable=BROWSER, transport=transport, storage_state=odd) as third:
        third.page.goto(f"{site.base}/storage")
        assert third.page.find("#out").inner_text() == "none|ok=2"


def test_pages_can_call_python_from_an_isolated_script(site):
    calls = []

    def on_call(call):
        calls.append((call.payload, call.frame_id == call.page.main_frame_id, call.page.url))
        call.reply("globalThis.__answer && globalThis.__answer(" + repr(len(calls)) + ")")

    script = ("globalThis.__answer = (n) => { document.documentElement.setAttribute('data-answer', n); };"
              "addEventListener('DOMContentLoaded', () => { __tell('ready:' + location.pathname); });")
    with Browser.launch(headless=True, executable=BROWSER, scripts=[(script, True)],
                        bindings={"__tell": (on_call, True)}) as b:
        p = b.page
        p.goto(f"{site.base}/frames")
        assert p.wait_for(lambda: len(calls) >= 3, 10), calls       # the page and both of its frames
        assert sorted(c[0] for c in calls) == ["ready:/frames", "ready:/inner", "ready:/inner"]
        assert sorted(c[1] for c in calls) == [False, False, True]
        assert p.wait_for(lambda: p.find("html[data-answer]", visible=False) is not None, 5)
        # The page's own scripts cannot see the isolated script or the binding.
        assert p.evaluate("function() { return typeof __tell + '/' + typeof __answer; }") == "undefined/undefined"
        p.run_in_world("document.documentElement.setAttribute('data-pushed', 'yes')")
        assert p.wait_for(lambda: p.find("html[data-pushed=yes]", visible=False) is not None, 5)


def test_a_browser_that_dies_is_reported_plainly(site):
    b = Browser.launch(headless=True, executable=BROWSER)
    try:
        p = b.page
        p.goto(f"{site.base}/")
        assert b.alive
        os.kill(b._process._pid, signal.SIGKILL)
        with pytest.raises(BrowserClosed):
            for _ in range(50):
                p.find("h1")
                time.sleep(0.1)
        assert not b.alive and p.closed
        with pytest.raises(BrowserClosed):
            p.goto(f"{site.base}/")
    finally:
        profile = b._process._profile
        b.close()
        b.close()   # closing twice is fine
    assert not profile.exists()


def test_missing_browser_is_explained(tmp_path, monkeypatch):
    with pytest.raises(BrowserNotFound, match="NEWSWIRE_BROWSER_PATH"):
        robot.find_browser(tmp_path / "no-browser-here")
    monkeypatch.setattr("app.robot.chrome.browser_candidates", lambda headless=True: [tmp_path / "nothing"])
    with pytest.raises(BrowserNotFound, match="Install Google Chrome"):
        robot.find_browser()
    result = robot.self_check()
    assert result["ok"] is False and "Install Google Chrome" in result["message"]
    not_a_browser = tmp_path / "fake-browser"
    not_a_browser.write_text("#!/bin/sh\necho 'cannot open display' >&2\nexit 3\n")
    not_a_browser.chmod(0o755)
    with pytest.raises(robot.BrowserLaunchError):
        Browser.launch(headless=True, executable=not_a_browser)
    with pytest.raises(robot.BrowserLaunchError, match="no screen"):
        Browser.launch(headless=False, executable=not_a_browser)


def test_websocket_refuses_something_that_is_not_a_debugging_port(site):
    port = int(site.base.rsplit(":", 1)[1])
    with pytest.raises(BrowserClosed):
        WebSocketTransport("127.0.0.1", port, "/devtools/browser/x")
