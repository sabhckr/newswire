"""Everything the robot engine raises. All of it derives from `RobotError`."""

from __future__ import annotations


class RobotError(Exception):
    """Base class: any problem driving the browser."""


class BrowserNotFound(RobotError):
    """No Chromium-family browser is installed where the engine looks."""


class BrowserLaunchError(RobotError):
    """The browser was found but did not start (no screen, missing libraries, crashed...)."""


class BrowserClosed(RobotError):
    """The browser, or the page being driven, went away (closed by a person, crashed, killed)."""


class ProtocolError(RobotError):
    """The browser refused a command."""

    def __init__(self, method: str, message: str, code: int | None = None):
        super().__init__(f"{method}: {message}")
        self.method = method
        self.message = message
        self.code = code


class RobotTimeout(RobotError):
    """Something did not happen in time."""


class NavigationError(RobotError):
    """The address could not be opened (DNS, refused connection, TLS...)."""


class Stale(RobotError):
    """The page moved on: the element or script context no longer exists. Look the element up again."""


class ScriptError(RobotError):
    """JavaScript run in the page threw an exception."""
