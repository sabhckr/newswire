"""Browser, Page, Frame and Element: what the rest of NEWSWIRE uses to drive a website.

    with Browser.launch(headless=True, storage_state=saved) as browser:
        page = browser.page
        page.goto("https://example.com/admin/login")
        page.find("input[type=password]").fill("…")
        page.find_button("Sign in").click()
        page.settle()
        saved = browser.storage_state()

Everything is synchronous and may be called from any thread except the connection's reader.
Elements are looked up fresh each time (`find` returns None when nothing matches); an element
that outlives its page raises `Stale`.
"""

from __future__ import annotations

import base64
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.robot import scripts
from app.robot.cdp import Connection, Pending
from app.robot.chrome import ChromeProcess, find_browser
from app.robot.errors import (BrowserClosed, NavigationError, ProtocolError, RobotError, RobotTimeout, ScriptError,
                              Stale)

log = logging.getLogger(__name__)

# Scripts and bindings marked `isolated` live in this separate JavaScript world: they see the
# page's DOM, but the page's own scripts cannot see or call them.
WORLD = "newswire_robot"

_STALE_HINTS = ("cannot find context", "context was destroyed", "find object with given id", "no node with given id",
                "node with given id", "default execution context", "target navigated or closed", "uniquecontextid",
                "promise was collected", "session with given id not found", "frame with the given id",
                "does not belong to the document", "node is detached")
_NO_VALUE_FILL = {"input:date", "input:time", "input:datetime-local", "input:month", "input:week", "input:color",
                  "input:range", "input:hidden"}
_NOT_TEXT = {"input:checkbox", "input:radio", "input:file", "input:submit", "input:button", "input:image",
             "input:reset"}
_KEYS = {
    "Enter": (13, "Enter", "\r"), "Tab": (9, "Tab", ""), "Escape": (27, "Escape", ""),
    "Backspace": (8, "Backspace", ""), "Delete": (46, "Delete", ""), "ArrowDown": (40, "ArrowDown", ""),
    "ArrowUp": (38, "ArrowUp", ""), "ArrowLeft": (37, "ArrowLeft", ""), "ArrowRight": (39, "ArrowRight", ""),
    "Home": (36, "Home", ""), "End": (35, "End", ""), " ": (32, "Space", " "),
}
_NET_ERRORS = {
    "net::ERR_NAME_NOT_RESOLVED": "the address does not exist (check the spelling)",
    "net::ERR_CONNECTION_REFUSED": "the website refused the connection",
    "net::ERR_CONNECTION_TIMED_OUT": "the website did not answer",
    "net::ERR_TIMED_OUT": "the website did not answer",
    "net::ERR_INTERNET_DISCONNECTED": "this computer is not connected to the internet",
    "net::ERR_ADDRESS_UNREACHABLE": "the website could not be reached",
    "net::ERR_CONNECTION_RESET": "the website dropped the connection",
    "net::ERR_ABORTED": "the website sent a download or nothing at all instead of a page",
    "net::ERR_TOO_MANY_REDIRECTS": "the website redirects in a circle",
}
_IGNORED_REQUEST_TYPES = {"EventSource", "WebSocket", "Media", "Ping", "Prefetch", "CSPViolationReport"}
_LONG_REQUEST_SECONDS = 10.0


def _is_stale(exc: ProtocolError) -> bool:
    low = exc.message.lower()
    return any(hint in low for hint in _STALE_HINTS)


@dataclass
class _Context:
    frame_id: str
    default: bool
    name: str


@dataclass
class BindingCall:
    """A message from the page: `payload` is the string the page passed to the binding."""

    page: "Page"
    frame_id: str | None
    session: str
    context_id: int
    name: str
    payload: str

    def reply(self, expression: str) -> None:
        """Run `expression` in the JavaScript world that called, without waiting for it."""
        self.page.browser._conn.post("Runtime.evaluate", {"expression": expression, "contextId": self.context_id},
                                     self.session)


class Element:
    """One element in a page. Obtained from `find…`; valid until its document is replaced."""

    def __init__(self, frame: "Frame", session: str, object_id: str):
        self.frame = frame
        self.session = session
        self.object_id = object_id

    @property
    def page(self) -> "Page":
        return self.frame.page

    def call(self, function: str, *args: Any, timeout: float = 30.0) -> Any:
        """Run `function(element, *args)` in the page and return its (JSON) value."""
        return self.page._call(self.frame.frame_id, function, (self, *args), by_value=True, timeout=timeout)

    def call_handle(self, function: str, *args: Any) -> "Element | None":
        return self.page._call(self.frame.frame_id, function, (self, *args), by_value=False)

    def _dom(self, method: str, **params: Any) -> dict:
        try:
            return self.page._conn.send(method, {"objectId": self.object_id, **params}, self.session)
        except ProtocolError as exc:
            if _is_stale(exc):
                raise Stale("The element is no longer on the page.") from exc
            raise

    # -- looking -----------------------------------------------------------------------------------

    def is_visible(self) -> bool:
        return bool(self.call(scripts.IS_VISIBLE))

    def kind(self) -> str:
        """'editable', 'textarea', 'select', 'iframe', 'input:<type>' or the tag name."""
        return self.call(scripts.KIND)

    def inner_text(self) -> str:
        return self.call(scripts.INNER_TEXT) or ""

    def attribute(self, name: str) -> str | None:
        return self.call(scripts.ATTRIBUTE, name)

    def value(self) -> str:
        return self.call(scripts.GET_VALUE) or ""

    def find(self, *selectors: str, visible: bool = True, hidden_ok: bool = False) -> "Element | None":
        return self.frame.find(*selectors, visible=visible, hidden_ok=hidden_ok, root=self)

    def find_button(self, text: str) -> "Element | None":
        return self.frame.find_button(text, root=self)

    def closest(self, selector: str) -> "Element | None":
        return self.call_handle(scripts.CLOSEST, selector)

    def content_frame(self) -> "Frame | None":
        """The document inside this <iframe>, or None when this is not a frame."""
        node = self._dom("DOM.describeNode").get("node") or {}
        frame_id = node.get("frameId")
        return Frame(self.page, frame_id) if frame_id else None

    # -- acting ------------------------------------------------------------------------------------

    def focus(self) -> None:
        self.call(scripts.FOCUS)

    def click(self, timeout: float = 5.0) -> None:
        """A real mouse click in the middle of the element. Waits up to `timeout` for it to stop
        moving (a dialog still sliding in) and for whatever covers or disables it to go away; an
        element that has no box at all (a hidden input) gets a script click instead."""
        page = self.page
        deadline = time.monotonic() + timeout
        centered = False
        while True:
            try:
                self._dom("DOM.scrollIntoViewIfNeeded")
            except ProtocolError:
                pass  # no layout box; the hit test below decides
            info = self.call(scripts.HIT_TEST)
            state = info.get("state")
            late = time.monotonic() > deadline
            if state == "gone":
                raise Stale("The element is no longer on the page.")
            if state == "hidden":
                break
            if info.get("disabled"):
                if late:
                    raise RobotError("The button stayed disabled (greyed out), so it could not be pressed.")
            elif state == "ok" and (late or not info.get("moving")):
                # Still moving after the whole wait (something that never rests): click where it is now.
                point = self._viewport_point(info)
                if point is None:
                    break
                page._mouse_click(*point)
                return
            elif late:
                break  # still covered: press it by script rather than give up
            if state == "covered" and not centered:
                self.call(scripts.CENTER)
                centered = True
            time.sleep(0.05)
        self.call(scripts.JS_CLICK)

    def _viewport_point(self, info: dict) -> tuple[float, float] | None:
        """Where the element's middle is in the window. None inside an out-of-process frame,
        where window coordinates are not known."""
        page = self.page
        if self.session != page.session:
            return None
        try:
            quads = self._dom("DOM.getContentQuads").get("quads") or []
        except (ProtocolError, Stale):
            quads = []
        for q in quads:
            xs, ys = q[0::2], q[1::2]
            if max(xs) - min(xs) >= 1 and max(ys) - min(ys) >= 1:
                return sum(xs) / 4, sum(ys) / 4
        if self.frame.frame_id == page.main_frame_id and "x" in info:
            return float(info["x"]), float(info["y"])
        return None

    def fill(self, text: str) -> None:
        """Replace what is in a text box, rich-text box or list with `text`, the way typing or
        pasting would (so the page's own scripts notice)."""
        text = "" if text is None else str(text)
        kind = self.call(scripts.PREPARE_FILL)
        if kind == "select":
            self.select_option(text)
            return
        if kind in _NOT_TEXT or not (kind in ("editable", "textarea") or kind.startswith("input:")):
            raise RobotError(f"Text cannot be typed into this part of the page (<{kind.replace('input:', 'input type=')}>).")
        if kind not in _NO_VALUE_FILL and text:
            self.page._insert_text(text, self.session)
            got = self.value()
            if kind == "editable":
                if got.strip() or not text.strip():
                    return
            elif _same_text(got, text, single_line=kind.startswith("input:")):
                return
        self.call(scripts.SET_VALUE, text)

    def press(self, key: str) -> None:
        self.focus()
        self.page._press(key, self.session)

    def select_option(self, label: str) -> bool:
        """Choose the option shown as `label` in a <select>. False when there is no such option."""
        return bool(self.call(scripts.SELECT_OPTION, label))

    def set_files(self, *paths: str | os.PathLike) -> None:
        """Put files into an <input type=file> (also a hidden one)."""
        self._dom("DOM.setFileInputFiles", files=[str(p) for p in paths])


def _same_text(got: str, want: str, *, single_line: bool) -> bool:
    """Did typing put `want` in the box? A box with a length limit keeps the start of it, as it
    would for a person."""
    got, want = got.replace("\r\n", "\n"), want.replace("\r\n", "\n")
    if single_line:
        want = want.replace("\n", "")
    return got == want or (bool(got) and want.startswith(got))


class Frame:
    """A document: the page itself (`page.main_frame`) or one inside an <iframe>."""

    def __init__(self, page: "Page", frame_id: str):
        self.page = page
        self.frame_id = frame_id

    @property
    def is_main(self) -> bool:
        return self.frame_id == self.page.main_frame_id

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Frame) and other.page is self.page and other.frame_id == self.frame_id

    def __hash__(self) -> int:
        return hash((id(self.page), self.frame_id))

    def evaluate(self, function: str, *args: Any, timeout: float = 30.0) -> Any:
        """Run `function(*args)` in this document and return its (JSON) value."""
        return self.page._call(self.frame_id, function, args, by_value=True, timeout=timeout)

    def evaluate_handle(self, function: str, *args: Any) -> Element | None:
        return self.page._call(self.frame_id, function, args, by_value=False)

    def _retry(self, fn: Callable[[], Any]) -> Any:
        """Looking something up while the page is changing documents: try again, briefly."""
        for attempt in range(3):
            try:
                return fn()
            except Stale:
                if attempt == 2:
                    raise
                time.sleep(0.25)
        return None

    def find(self, *selectors: str, visible: bool = True, hidden_ok: bool = False,
             root: Element | None = None) -> Element | None:
        """The first element matching the CSS selectors, tried in order. `visible` (the default)
        skips elements that are not shown; `hidden_ok` falls back to a hidden match."""
        mode = "prefer-visible" if hidden_ok else ("visible" if visible else "any")
        return self._retry(lambda: self.evaluate_handle(scripts.FIND, root, list(selectors), mode))

    def count(self, *selectors: str, root: Element | None = None) -> int:
        return int(self._retry(lambda: self.evaluate(scripts.COUNT, root, list(selectors))) or 0)

    def find_button(self, text: str, root: Element | None = None) -> Element | None:
        return self._retry(lambda: self.evaluate_handle(scripts.FIND_BUTTON, root, text))

    def find_text(self, text: str, root: Element | None = None) -> Element | None:
        return self._retry(lambda: self.evaluate_handle(scripts.FIND_TEXT, root, text))

    def find_link(self, text: str = "", pattern: str | None = None, root: Element | None = None) -> Element | None:
        return self._retry(lambda: self.evaluate_handle(scripts.FIND_LINK, root, text, pattern))

    def body(self) -> Element | None:
        return self._retry(lambda: self.evaluate_handle("function() { return document.body; }"))


class Page:
    """One tab."""

    def __init__(self, browser: "Browser", session: str, info: dict):
        self.browser = browser
        self._conn: Connection = browser._conn
        self.session = session
        self.target_id: str = info.get("targetId", "")
        self.main_frame_id: str = self.target_id
        self.url: str = info.get("url") or "about:blank"
        self.dialogs: list[str] = []  # messages of alert/confirm boxes the page showed
        self.crashed = False
        self._closed = False
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._contexts: dict[tuple[str, int], _Context] = {}
        self._default: dict[str, tuple[str, int]] = {}
        self._worlds: dict[tuple[str, str], tuple[str, int]] = {}
        self._parents: dict[str, str] = {}
        self._children: set[str] = set()
        self._loading = False
        self._loader: str | None = None
        self._dcl: list[str] = []
        self._inflight: dict[str, tuple[float, bool]] = {}
        self._last_activity = time.monotonic()
        self._dialog_open = False
        self._chooser: tuple[dict, str] | None = None
        self._chooser_event = threading.Event()
        self._ready: Pending | None = None

    # -- set-up (reader thread: post only) ---------------------------------------------------------

    def _post(self, method: str, params: dict | None = None, session: str | None = None) -> Pending:
        return self._conn.post(method, params, session or self.session)

    def _setup(self, waiting: bool) -> None:
        self._post("Page.enable")
        self._post("Page.setLifecycleEventsEnabled", {"enabled": True})
        self._post("Runtime.enable")
        self._post("Network.enable")
        self._post("Emulation.setFocusEmulationEnabled", {"enabled": True})
        self._post("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True})
        self._install(self.session)
        self._ready = self._post("Page.getFrameTree")
        if waiting:
            self._post("Runtime.runIfWaitingForDebugger")

    def _setup_child(self, session: str, waiting: bool) -> None:
        """An <iframe> from another site runs in its own process with its own session."""
        with self._lock:
            self._children.add(session)
        self._post("Page.enable", session=session)
        self._post("Runtime.enable", session=session)
        self._post("Network.enable", session=session)
        self._post("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True},
                   session)
        self._install(session)
        if waiting:
            self._post("Runtime.runIfWaitingForDebugger", session=session)

    def _install(self, session: str) -> None:
        for name, (_callback, isolated) in list(self.browser._bindings.items()):
            self._post("Runtime.addBinding",
                       {"name": name, "executionContextName": WORLD} if isolated else {"name": name}, session)
        for source, isolated in list(self.browser._scripts):
            params: dict[str, Any] = {"source": source, "runImmediately": True}
            if isolated:
                params["worldName"] = WORLD
            self._post("Page.addScriptToEvaluateOnNewDocument", params, session)

    def wait_ready(self, timeout: float = 20.0) -> None:
        if self._ready is None:
            return
        try:
            tree = self._ready.wait(timeout)
        except ProtocolError:
            return
        frame = (tree.get("frameTree") or {}).get("frame") or {}
        with self._lock:
            if frame.get("id"):
                self.main_frame_id = frame["id"]
            if frame.get("url") and self.url in ("", "about:blank"):
                self.url = frame["url"]

    # -- events (reader thread) --------------------------------------------------------------------

    def _on_event(self, method: str, params: dict, session: str) -> None:
        handler = _PAGE_EVENTS.get(method)
        if handler is not None:
            handler(self, params, session)

    def _ev_context_created(self, params: dict, session: str) -> None:
        ctx = params.get("context") or {}
        aux = ctx.get("auxData") or {}
        frame_id = aux.get("frameId")
        if not frame_id or "id" not in ctx:
            return
        key = (session, ctx["id"])
        with self._changed:
            self._contexts[key] = _Context(frame_id, bool(aux.get("isDefault")), ctx.get("name") or "")
            if aux.get("isDefault"):
                self._default[frame_id] = key
            elif ctx.get("name"):
                self._worlds[(frame_id, ctx["name"])] = key
            self._changed.notify_all()

    def _forget(self, key: tuple[str, int]) -> None:
        info = self._contexts.pop(key, None)
        if info is None:
            return
        if self._default.get(info.frame_id) == key:
            del self._default[info.frame_id]
        if self._worlds.get((info.frame_id, info.name)) == key:
            del self._worlds[(info.frame_id, info.name)]

    def _ev_context_destroyed(self, params: dict, session: str) -> None:
        with self._changed:
            self._forget((session, params.get("executionContextId")))

    def _ev_contexts_cleared(self, params: dict, session: str) -> None:
        with self._changed:
            for key in [k for k in self._contexts if k[0] == session]:
                self._forget(key)

    def _ev_binding(self, params: dict, session: str) -> None:
        entry = self.browser._bindings.get(params.get("name"))
        if entry is None:
            return
        ctx_id = params.get("executionContextId")
        with self._lock:
            info = self._contexts.get((session, ctx_id))
        entry[0](BindingCall(self, info.frame_id if info else None, session, ctx_id, params.get("name"),
                             params.get("payload") or ""))

    def _ev_frame_navigated(self, params: dict, session: str) -> None:
        frame = params.get("frame") or {}
        frame_id = frame.get("id")
        with self._changed:
            if frame.get("parentId"):
                self._parents[frame_id] = frame["parentId"]
            elif session == self.session and frame_id:
                self.main_frame_id = frame_id
                self.url = (frame.get("url") or "") + (frame.get("urlFragment") or "")
                self._loader = frame.get("loaderId")
                self._inflight.clear()
                self._last_activity = time.monotonic()
            self._changed.notify_all()

    def _ev_frame_attached(self, params: dict, session: str) -> None:
        with self._lock:
            if params.get("frameId") and params.get("parentFrameId"):
                self._parents[params["frameId"]] = params["parentFrameId"]

    def _ev_frame_detached(self, params: dict, session: str) -> None:
        if params.get("reason") == "swap":
            return  # the frame moved to another process; it still exists
        with self._lock:
            self._parents.pop(params.get("frameId"), None)

    def _ev_same_document(self, params: dict, session: str) -> None:
        if session == self.session and params.get("frameId") == self.main_frame_id and params.get("url"):
            with self._changed:
                self.url = params["url"]
                self._changed.notify_all()

    def _ev_loading(self, started: bool, params: dict, session: str) -> None:
        if session == self.session and params.get("frameId") == self.main_frame_id:
            with self._changed:
                self._loading = started
                self._last_activity = time.monotonic()
                self._changed.notify_all()

    def _ev_lifecycle(self, params: dict, session: str) -> None:
        if session != self.session or params.get("frameId") != self.main_frame_id:
            return
        if params.get("name") in ("DOMContentLoaded", "load"):
            with self._changed:
                loader = params.get("loaderId")
                if loader and loader not in self._dcl:
                    self._dcl.append(loader)
                    del self._dcl[:-50]
                self._changed.notify_all()

    def _ev_dialog_opening(self, params: dict, session: str) -> None:
        self.dialogs.append(str(params.get("message") or ""))
        del self.dialogs[:-20]
        # Without a window nobody can answer, and a "leave this page?" box would block the robot:
        # say OK. In a visible window a question such as "Delete this post?" is the person's to answer.
        if self.browser.headless or params.get("type") == "beforeunload":
            self._post("Page.handleJavaScriptDialog", {"accept": True}, session)
        else:
            self._dialog_open = True

    def _ev_dialog_closed(self, params: dict, session: str) -> None:
        self._dialog_open = False

    def _ev_file_chooser(self, params: dict, session: str) -> None:
        self._chooser = (params, session)
        self._chooser_event.set()

    def _ev_request(self, params: dict, session: str) -> None:
        if params.get("type") in _IGNORED_REQUEST_TYPES:
            return
        url = (params.get("request") or {}).get("url") or ""
        if url.startswith(("data:", "blob:", "about:")):
            return
        # A GET that stays open (long polling, a stream) is not waited for beyond a few seconds;
        # anything that sends data (saving a form, uploading a photo) is, however long it takes.
        patient = ((params.get("request") or {}).get("method") or "GET").upper() != "GET"
        with self._lock:
            self._inflight[params.get("requestId")] = (time.monotonic(), patient)
            self._last_activity = time.monotonic()

    def _ev_request_done(self, params: dict, session: str) -> None:
        # Any of the page's sessions: a frame from another site starts loading in the page's
        # process and finishes in its own.
        with self._lock:
            if self._inflight.pop(params.get("requestId"), None) is not None:
                self._last_activity = time.monotonic()

    def _ev_crashed(self, params: dict, session: str) -> None:
        if session == self.session:
            self.crashed = True
            self._mark_closed()

    def _mark_closed(self) -> None:
        with self._changed:
            self._closed = True
            self._changed.notify_all()
        self._chooser_event.set()

    def _child_detached(self, session: str) -> None:
        with self._changed:
            self._children.discard(session)
            for key in [k for k in self._contexts if k[0] == session]:
                self._forget(key)

    # -- state -------------------------------------------------------------------------------------

    @property
    def closed(self) -> bool:
        return self._closed or self._conn.closed

    @property
    def main_frame(self) -> Frame:
        return Frame(self, self.main_frame_id)

    def _check_open(self) -> None:
        if self.crashed:
            raise BrowserClosed("The page crashed.")
        if self.closed:
            raise BrowserClosed("The browser window was closed.")

    # -- running script ----------------------------------------------------------------------------

    def _context_for(self, frame_id: str, timeout: float = 8.0) -> tuple[str, int]:
        deadline = time.monotonic() + timeout
        with self._changed:
            while True:
                self._check_open()
                key = self._default.get(frame_id)
                if key is not None:
                    return key
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Stale("The page is not ready for the robot yet.")
                self._changed.wait(min(remaining, 0.25))

    def _call(self, frame_id: str, function: str, args: tuple, *, by_value: bool, timeout: float = 30.0) -> Any:
        self._check_open()
        if self._dialog_open:
            raise Stale("A message box is open in the page.")
        target = next((a for a in args if isinstance(a, Element)), None)
        params: dict[str, Any] = {
            "functionDeclaration": function,
            "arguments": [{"objectId": a.object_id} if isinstance(a, Element) else {"value": a} for a in args],
            "awaitPromise": True, "userGesture": True, "returnByValue": by_value,
        }
        if target is not None:
            session = target.session
            params["objectId"] = target.object_id
        else:
            session, params["executionContextId"] = self._context_for(frame_id)
        try:
            res = self._conn.send("Runtime.callFunctionOn", params, session, timeout=timeout)
        except ProtocolError as exc:
            if _is_stale(exc):
                raise Stale("The page changed while the robot was working on it.") from exc
            raise
        except BrowserClosed:
            self._check_open()
            raise
        details = res.get("exceptionDetails")
        if details:
            exception = details.get("exception") or {}
            text = str(exception.get("description") or exception.get("value") or details.get("text") or "error")
            if any(hint in text.lower() for hint in _STALE_HINTS):
                raise Stale("The page changed while the robot was working on it.")
            raise ScriptError(text.splitlines()[0][:300])
        result = res.get("result") or {}
        if by_value:
            return result.get("value")
        if result.get("subtype") == "node" and result.get("objectId"):
            return Element(Frame(self, frame_id), session, result["objectId"])
        return None

    def evaluate(self, function: str, *args: Any, timeout: float = 30.0) -> Any:
        return self.main_frame.evaluate(function, *args, timeout=timeout)

    def run_in_world(self, expression: str) -> None:
        """Run `expression` in the isolated world of every frame (no waiting, no result)."""
        with self._lock:
            keys = [key for (_frame, name), key in self._worlds.items() if name == WORLD]
        for session, ctx_id in keys:
            self._post("Runtime.evaluate", {"expression": expression, "contextId": ctx_id}, session)

    # -- finding (in the page's own document) ------------------------------------------------------

    def find(self, *selectors: str, visible: bool = True, hidden_ok: bool = False,
             root: Element | None = None) -> Element | None:
        return self.main_frame.find(*selectors, visible=visible, hidden_ok=hidden_ok, root=root)

    def count(self, *selectors: str) -> int:
        return self.main_frame.count(*selectors)

    def find_button(self, text: str, root: Element | None = None) -> Element | None:
        return self.main_frame.find_button(text, root)

    def find_text(self, text: str, root: Element | None = None) -> Element | None:
        return self.main_frame.find_text(text, root)

    def find_link(self, text: str = "", pattern: str | None = None) -> Element | None:
        return self.main_frame.find_link(text, pattern)

    def frame_owner(self, frame_id: str) -> Element | None:
        """The <iframe> element (in its parent document) that holds frame `frame_id`."""
        with self._lock:
            parent = self._parents.get(frame_id) or self.main_frame_id
            parent_key = self._default.get(parent)
            sessions = [parent_key[0]] if parent_key else []
            sessions += [s for s in (self.session, *self._children) if s not in sessions]
        for session in sessions:
            try:
                backend = self._conn.send("DOM.getFrameOwner", {"frameId": frame_id}, session,
                                          timeout=10).get("backendNodeId")
                if backend is None:
                    continue
                ctx_session, ctx_id = self._context_for(parent)
                if ctx_session != session:
                    continue
                obj = self._conn.send("DOM.resolveNode", {"backendNodeId": backend, "executionContextId": ctx_id},
                                      session, timeout=10).get("object") or {}
            except (ProtocolError, Stale):
                continue
            if obj.get("objectId"):
                return Element(Frame(self, parent), session, obj["objectId"])
        return None

    # -- moving around -----------------------------------------------------------------------------

    def goto(self, url: str, timeout: float = 45.0) -> None:
        """Open `url` and wait until its document has been read (not for every image)."""
        self._check_open()
        with self._lock:
            before = self._loader
        deadline = time.monotonic() + timeout
        try:
            res = self._conn.send("Page.navigate", {"url": url}, self.session, timeout=timeout)
        except RobotTimeout as exc:
            raise RobotTimeout(f"{url} did not answer within {timeout:g} seconds.") from exc
        except BrowserClosed:
            self._check_open()
            raise
        error = res.get("errorText")
        if error:
            raise NavigationError(f"{url} could not be opened: {_NET_ERRORS.get(error, error)}.")
        loader = res.get("loaderId")
        if not loader:
            return  # same document (only the #fragment changed)
        with self._changed:
            while True:
                self._check_open()
                if loader in self._dcl:
                    return
                if self._loader not in (None, before, loader) and self._loader in self._dcl:
                    return  # the page moved on to another address by itself
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RobotTimeout(f"{url} did not finish loading within {timeout:g} seconds.")
                self._changed.wait(min(remaining, 0.25))

    def settle(self, timeout: float = 20.0, quiet: float = 0.5) -> bool:
        """Wait until the page has stopped loading and has been off the network for `quiet`
        seconds. True when it went quiet; False when `timeout` ran out first (a busy page)."""
        start = time.monotonic()
        deadline = start + timeout
        while True:
            if self.closed:
                return False
            now = time.monotonic()
            with self._lock:
                busy = self._loading or any(patient or now - t < _LONG_REQUEST_SECONDS
                                            for t, patient in self._inflight.values())
                last = self._last_activity
            if not busy and now - max(last, start) >= quiet:
                return True
            if now >= deadline:
                return False
            time.sleep(0.05)

    def wait_for(self, condition: Callable[[], Any], timeout: float = 10.0, interval: float = 0.1) -> bool:
        """Poll `condition` until it is true. A condition that raises `Stale` counts as not yet."""
        deadline = time.monotonic() + timeout
        while True:
            self._check_open()
            try:
                if condition():
                    return True
            except Stale:
                pass
            if time.monotonic() >= deadline:
                return False
            time.sleep(interval)

    def wait_for_url_change(self, before: str, timeout: float = 15.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._changed:
            while self.url == before:
                if self.closed:
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._changed.wait(min(remaining, 0.25))
        return True

    # -- input -------------------------------------------------------------------------------------

    def _mouse_click(self, x: float, y: float) -> None:
        base = {"x": x, "y": y, "button": "left", "clickCount": 1}
        self._conn.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y}, self.session)
        self._conn.send("Input.dispatchMouseEvent", {**base, "type": "mousePressed", "buttons": 1}, self.session)
        release = self._post("Input.dispatchMouseEvent", {**base, "type": "mouseReleased", "buttons": 0})
        deadline = time.monotonic() + 30
        while not release.done:
            # A click that opens a message box in a visible window is answered by the person.
            if self._dialog_open or self.closed or time.monotonic() > deadline:
                return
            time.sleep(0.01)
        try:
            release.wait(0)
        except (ProtocolError, BrowserClosed):
            pass  # the click closed or replaced the page

    def _insert_text(self, text: str, session: str | None = None) -> None:
        self._conn.send("Input.insertText", {"text": text}, session or self.session,
                        timeout=30 + len(text) / 20_000)

    def insert_text(self, text: str) -> None:
        """Type `text` into whatever has the keyboard focus."""
        self._insert_text(text)

    def _press(self, key: str, session: str | None = None) -> None:
        if key not in _KEYS:
            raise ValueError(f"unknown key {key!r}")
        code, name, text = _KEYS[key]
        event = {"key": key, "code": name, "windowsVirtualKeyCode": code, "nativeVirtualKeyCode": code}
        down = {**event, "type": "keyDown" if text else "rawKeyDown"}
        if text:
            down["text"] = text
        self._conn.send("Input.dispatchKeyEvent", down, session or self.session)
        self._post("Input.dispatchKeyEvent", {**event, "type": "keyUp"}, session or self.session)

    def press(self, key: str) -> None:
        self._press(key)

    def choose_files(self, opener: Element, *paths: str | os.PathLike, timeout: float = 15.0) -> None:
        """Click `opener` (an upload button) and answer the file dialog it opens with `paths`."""
        self._chooser = None
        self._chooser_event.clear()
        self._conn.send("Page.setInterceptFileChooserDialog", {"enabled": True}, self.session)
        try:
            opener.click()
            if not self._chooser_event.wait(timeout) or self._chooser is None:
                self._check_open()
                raise RobotTimeout("Clicking the upload button did not open a file dialog.")
            params, session = self._chooser
            if params.get("backendNodeId") is None:
                raise RobotError("The browser did not say which upload box the file dialog belongs to.")
            self._conn.send("DOM.setFileInputFiles",
                            {"files": [str(p) for p in paths], "backendNodeId": params["backendNodeId"]}, session)
        finally:
            self._post("Page.setInterceptFileChooserDialog", {"enabled": False})

    # -- pictures ----------------------------------------------------------------------------------

    def screenshot(self, path: str | os.PathLike, full_page: bool = True) -> None:
        params: dict[str, Any] = {"format": "png"}
        if full_page:
            try:
                size = self.evaluate(scripts.PAGE_SIZE, timeout=10) or {}
            except RobotError:
                size = {}
            if size.get("width") and size.get("height"):
                params["captureBeyondViewport"] = True
                params["clip"] = {"x": 0, "y": 0, "width": min(int(size["width"]), 4000),
                                  "height": min(int(size["height"]), 12000), "scale": 1}
        data = self._conn.send("Page.captureScreenshot", params, self.session, timeout=30).get("data") or ""
        Path(path).write_bytes(base64.b64decode(data))


_PAGE_EVENTS: dict[str, Callable[[Page, dict, str], None]] = {
    "Runtime.executionContextCreated": Page._ev_context_created,
    "Runtime.executionContextDestroyed": Page._ev_context_destroyed,
    "Runtime.executionContextsCleared": Page._ev_contexts_cleared,
    "Runtime.bindingCalled": Page._ev_binding,
    "Page.frameNavigated": Page._ev_frame_navigated,
    "Page.frameAttached": Page._ev_frame_attached,
    "Page.frameDetached": Page._ev_frame_detached,
    "Page.navigatedWithinDocument": Page._ev_same_document,
    "Page.frameStartedLoading": lambda page, params, session: page._ev_loading(True, params, session),
    "Page.frameStoppedLoading": lambda page, params, session: page._ev_loading(False, params, session),
    "Page.lifecycleEvent": Page._ev_lifecycle,
    "Page.javascriptDialogOpening": Page._ev_dialog_opening,
    "Page.javascriptDialogClosed": Page._ev_dialog_closed,
    "Page.fileChooserOpened": Page._ev_file_chooser,
    "Network.requestWillBeSent": Page._ev_request,
    "Network.loadingFinished": Page._ev_request_done,
    "Network.loadingFailed": Page._ev_request_done,
    "Inspector.targetCrashed": Page._ev_crashed,
}


class Browser:
    """A running browser with one profile. Use `Browser.launch(...)`, and close it when done."""

    def __init__(self, process: ChromeProcess, *, storage_state: dict | None = None,
                 scripts_: list[tuple[str, bool]] | None = None,
                 bindings: dict[str, tuple[Callable[[BindingCall], None], bool]] | None = None):
        self._process = process
        self._conn: Connection = process.connection
        self.headless = process.headless
        self.version = process.version
        self.executable = process.executable
        self._scripts: list[tuple[str, bool]] = list(scripts_ or [])
        self._bindings: dict[str, tuple[Callable[[BindingCall], None], bool]] = dict(bindings or {})
        self._pages: list[Page] = []
        self._sessions: dict[str, Page] = {}
        self._guard = threading.Condition()
        self._origins: dict[str, list[dict]] = {}
        try:
            self._restore(storage_state or {})
            self._conn.subscribe(self._on_event)
            self._conn.post("Browser.setDownloadBehavior", {"behavior": "deny"})
            self._conn.send("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": True,
                                                     "flatten": True})
            if not self._wait_for_page(10):
                self._conn.send("Target.createTarget", {"url": "about:blank"})
                if not self._wait_for_page(15):
                    raise BrowserClosed("The browser started without a window.")
            self.page.wait_ready()
        except BaseException:
            self.close()
            raise

    @classmethod
    def launch(cls, *, headless: bool = True, executable: str | os.PathLike | None = None,
               storage_state: dict | None = None, transport: str = "auto",
               scripts: list[tuple[str, bool]] | None = None,
               bindings: dict[str, tuple[Callable[[BindingCall], None], bool]] | None = None,
               window: tuple[int, int] = (1280, 900)) -> "Browser":
        """Start a browser. `scripts` are (source, isolated) pairs run in every new document;
        `bindings` maps a function name the page can call to (callback, isolated)."""
        path = find_browser(executable, headless=headless)
        process = ChromeProcess(path, headless=headless, transport=transport, window=window)
        return cls(process, storage_state=storage_state, scripts_=scripts, bindings=bindings)

    def __enter__(self) -> "Browser":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- targets (reader thread) -------------------------------------------------------------------

    def _on_event(self, method: str, params: dict, session: str | None) -> None:
        if method == "Target.attachedToTarget":
            self._attached(params, session)
        elif method == "Target.detachedFromTarget":
            self._detached(params.get("sessionId"))
        elif session is not None:
            page = self._sessions.get(session)
            if page is not None:
                page._on_event(method, params, session)

    def _attached(self, params: dict, parent: str | None) -> None:
        info = params.get("targetInfo") or {}
        session = params.get("sessionId")
        waiting = bool(params.get("waitingForDebugger"))
        kind = info.get("type")
        if parent is None and kind == "page" and not info.get("subtype"):
            page = Page(self, session, info)
            with self._guard:
                self._sessions[session] = page
                self._pages.append(page)
                self._guard.notify_all()
            page._setup(waiting)
            return
        owner = self._sessions.get(parent) if parent else None
        if owner is not None and kind == "iframe":
            with self._guard:
                self._sessions[session] = owner
            owner._setup_child(session, waiting)
            return
        # Workers, extensions, the browser's own UI: let them run and stop listening to them.
        if waiting:
            self._conn.post("Runtime.runIfWaitingForDebugger", None, session)
        self._conn.post("Target.detachFromTarget", {"sessionId": session}, parent)

    def _detached(self, session: str | None) -> None:
        with self._guard:
            page = self._sessions.pop(session, None)
        if page is None:
            return
        if page.session == session:
            page._mark_closed()
        else:
            page._child_detached(session)

    def _wait_for_page(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._guard:
            while not self._pages:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._conn.closed:
                    return False
                self._guard.wait(min(remaining, 0.25))
        return True

    # -- state -------------------------------------------------------------------------------------

    @property
    def page(self) -> Page:
        """The first tab that is still open (the one the browser started with)."""
        with self._guard:
            for page in self._pages:
                if not page.closed:
                    return page
            if self._pages:
                return self._pages[0]
        raise BrowserClosed("The browser has no window.")

    @property
    def pages(self) -> list[Page]:
        with self._guard:
            return [p for p in self._pages if not p.closed]

    @property
    def alive(self) -> bool:
        """The browser is running and at least one of its tabs is open."""
        return not self._conn.closed and self._process.alive and bool(self.pages)

    # -- sign-in state -----------------------------------------------------------------------------

    def _restore(self, state: dict) -> None:
        now = time.time()
        cookies = []
        for c in state.get("cookies") or []:
            if not isinstance(c, dict) or not c.get("name") or not c.get("domain"):
                continue
            expires = c.get("expires")
            if isinstance(expires, (int, float)) and 0 < expires < now:
                continue
            param: dict[str, Any] = {"name": c["name"], "value": c.get("value") or "", "domain": c["domain"],
                                     "path": c.get("path") or "/", "secure": bool(c.get("secure")),
                                     "httpOnly": bool(c.get("httpOnly"))}
            if isinstance(expires, (int, float)) and expires > 0:
                param["expires"] = expires
            if c.get("sameSite") in ("Strict", "Lax", "None"):
                param["sameSite"] = c["sameSite"]
            cookies.append(param)
        if cookies:
            try:
                self._conn.send("Storage.setCookies", {"cookies": cookies})
            except ProtocolError:
                for param in cookies:  # one odd cookie must not cost the whole sign-in
                    try:
                        self._conn.send("Storage.setCookies", {"cookies": [param]})
                    except ProtocolError:
                        log.info("robot: could not restore cookie %s for %s", param["name"], param["domain"])
        origins: dict[str, list[list[str]]] = {}
        for entry in state.get("origins") or []:
            if not isinstance(entry, dict) or not entry.get("origin"):
                continue
            items = [i for i in entry.get("localStorage") or [] if isinstance(i, dict) and "name" in i]
            if items:
                self._origins[entry["origin"]] = items
                origins[entry["origin"]] = [[str(i["name"]), str(i.get("value") or "")] for i in items]
        if origins:
            self._scripts.insert(0, (scripts.restore_local_storage(origins), False))

    def storage_state(self) -> dict:
        """Cookies and localStorage, to hand to `launch(storage_state=…)` next time. The shape is
        the one Playwright uses, so states saved by earlier NEWSWIRE versions still work."""
        raw = self._conn.send("Storage.getCookies").get("cookies") or []
        cookies = []
        for c in raw:
            cookie = {"name": c.get("name"), "value": c.get("value") or "", "domain": c.get("domain"),
                      "path": c.get("path") or "/", "expires": -1 if c.get("session") else c.get("expires", -1),
                      "httpOnly": bool(c.get("httpOnly")), "secure": bool(c.get("secure"))}
            if c.get("sameSite") in ("Strict", "Lax", "None"):
                cookie["sameSite"] = c["sameSite"]
            cookies.append(cookie)
        origins = dict(self._origins)
        for page in self.pages:
            with page._lock:
                frame_ids = list(page._default)
            for frame_id in frame_ids:
                try:
                    data = Frame(page, frame_id).evaluate(scripts.LOCAL_STORAGE, timeout=5)
                except RobotError:
                    continue
                if not data or not str(data.get("origin") or "").startswith("http"):
                    continue
                items = [i for i in data.get("items") or [] if i.get("name") != "__nw_restored"]
                if items:
                    origins[data["origin"]] = items
                else:
                    origins.pop(data["origin"], None)
        return {"cookies": cookies,
                "origins": [{"origin": origin, "localStorage": items} for origin, items in origins.items()]}

    def close(self) -> None:
        self._process.close()
        with self._guard:
            pages = list(self._pages)
        for page in pages:
            page._mark_closed()
