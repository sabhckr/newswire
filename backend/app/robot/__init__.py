"""NEWSWIRE's robot browser engine: pure Python, standard library only.

It drives a Chromium-family browser that is already installed on the computer (Google Chrome,
Chromium, Microsoft Edge, Brave) by speaking the Chrome DevTools Protocol to it directly. There
is no Playwright, no Selenium, no Node.js and no browser download.

* `chrome.py`  finds and starts the browser with a throwaway profile
* `cdp.py`     the connection (a private pipe; a local WebSocket on Windows)
* `browser.py` Browser, Page, Frame, Element: open pages, find things, type, click, upload
* `scripts.py` the JavaScript run inside pages

Check an installation with `python -m app.robot`.
"""

from app.robot.browser import WORLD, BindingCall, Browser, Element, Frame, Page
from app.robot.chrome import INSTALL_HINT, browser_candidates, find_browser
from app.robot.errors import (BrowserClosed, BrowserLaunchError, BrowserNotFound, NavigationError, ProtocolError,
                              RobotError, RobotTimeout, ScriptError, Stale)

__all__ = [
    "BindingCall", "Browser", "BrowserClosed", "BrowserLaunchError", "BrowserNotFound", "Element", "Frame",
    "INSTALL_HINT", "NavigationError", "Page", "ProtocolError", "RobotError", "RobotTimeout", "ScriptError", "Stale",
    "WORLD", "browser_candidates", "find_browser", "self_check",
]


def self_check(executable: str | None = None) -> dict:
    """Start the browser without a window, load a page, type into it and read it back.
    Returns {"ok", "browser", "version", "message"}; never raises."""
    out = {"ok": False, "browser": None, "version": None, "message": ""}
    try:
        path = find_browser(executable, headless=True)
        out["browser"] = str(path)
        with Browser.launch(headless=True, executable=path) as browser:
            out["version"] = browser.version
            page = browser.page
            page.goto("data:text/html,<title>robot</title><input id=q><button onclick=\"document.title=q.value\">go</button>")
            page.find("#q").fill("Samyabadi ঢাকা")
            page.find_button("go").click()
            title = page.evaluate("function() { return document.title; }")
            if title != "Samyabadi ঢাকা":
                raise RobotError(f"the test page answered {title!r}")
        out["ok"] = True
        out["message"] = f"The robot browser works: {out['version']} at {path}"
    except RobotError as exc:
        out["message"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        out["message"] = f"{type(exc).__name__}: {exc}"
    return out
