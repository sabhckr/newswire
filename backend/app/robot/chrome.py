"""Find a Chromium-family browser that is already on this computer, and start it for the robot.

Nothing is downloaded. The engine drives the browser the person already has: Google Chrome,
Chromium, Microsoft Edge or Brave (or a Chromium left behind by an earlier Playwright install).
Each run gets its own throwaway profile folder, so the person's own browser profile, windows and
sign-ins are never touched.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import weakref
from pathlib import Path

from app.robot.cdp import Connection, PipeTransport, WebSocketTransport
from app.robot.errors import BrowserLaunchError, BrowserNotFound, RobotError

log = logging.getLogger(__name__)

STARTUP_SECONDS = 30.0
INSTALL_HINT = ("Install Google Chrome (or Microsoft Edge, Brave or Chromium) on the computer that runs NEWSWIRE, "
                "or set NEWSWIRE_BROWSER_PATH to the browser's program file.")


def _mac_apps() -> list[Path]:
    names = [
        ("Google Chrome.app", "Google Chrome"),
        ("Chromium.app", "Chromium"),
        ("Microsoft Edge.app", "Microsoft Edge"),
        ("Brave Browser.app", "Brave Browser"),
        ("Google Chrome for Testing.app", "Google Chrome for Testing"),
    ]
    out = []
    for root in (Path("/Applications"), Path.home() / "Applications"):
        out += [root / app / "Contents" / "MacOS" / exe for app, exe in names]
    return out


def _linux_programs() -> list[Path]:
    out = []
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
                 "microsoft-edge-stable", "brave-browser"):
        found = shutil.which(name)
        if found:
            out.append(Path(found))
    return out + [Path("/usr/bin/chromium"), Path("/snap/bin/chromium"), Path("/opt/google/chrome/chrome")]


def _windows_programs() -> list[Path]:
    tails = [r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe",
             r"BraveSoftware\Brave-Browser\Application\brave.exe", r"Chromium\Application\chrome.exe"]
    out = []
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            out += [Path(base) / tail for tail in tails]
    return out


def _playwright_cache(headless: bool) -> list[Path]:
    """Browsers a previous Playwright install left on disk (newest first). Full Chromium first;
    the windowless "headless shell" only when no window is needed."""
    roots = []
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        roots.append(Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]))
    if sys.platform == "darwin":
        roots.append(Path.home() / "Library" / "Caches" / "ms-playwright")
    elif sys.platform == "win32":
        if os.environ.get("LOCALAPPDATA"):
            roots.append(Path(os.environ["LOCALAPPDATA"]) / "ms-playwright")
    else:
        roots.append(Path.home() / ".cache" / "ms-playwright")
    full = ["*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
            "*/Chromium.app/Contents/MacOS/Chromium", "*/chrome", "*/chrome.exe"]
    shell = ["*/chrome-headless-shell", "*/headless_shell", "*/chrome-headless-shell.exe"]
    out: list[Path] = []
    for root in roots:
        try:
            folders = sorted(root.glob("chromium-*"), reverse=True)
            shells = sorted(root.glob("chromium_headless_shell-*"), reverse=True) if headless else []
        except OSError:
            continue
        for folder, patterns in [(f, full) for f in folders] + [(f, shell) for f in shells]:
            for pattern in patterns:
                out += sorted(folder.glob(pattern))
    return out


def browser_candidates(headless: bool = True) -> list[Path]:
    if sys.platform == "darwin":
        system = _mac_apps()
    elif sys.platform == "win32":
        system = _windows_programs()
    else:
        system = _linux_programs()
    return system + _playwright_cache(headless)


def _usable(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:
        return False


def find_browser(explicit: str | os.PathLike | None = None, *, headless: bool = True) -> Path:
    """The browser program to run. `explicit` (NEWSWIRE_BROWSER_PATH) wins when set."""
    if explicit:
        path = Path(explicit).expanduser()
        if path.suffix == ".app" and path.is_dir():  # a macOS application bundle was given
            inner = sorted((path / "Contents" / "MacOS").glob("*"))
            path = inner[0] if inner else path
        if not _usable(path):
            raise BrowserNotFound(f"NEWSWIRE_BROWSER_PATH points to “{explicit}”, which is not a browser program "
                                  "on this computer.")
        return path
    seen = set()
    for path in browser_candidates(headless):
        if path in seen:
            continue
        seen.add(path)
        if _usable(path):
            return path
    raise BrowserNotFound("No browser was found for the robot. " + INSTALL_HINT)


def _is_headless_shell(path: Path) -> bool:
    return "headless" in path.name.lower().replace("_", "-") and "shell" in path.name.lower()


def _arguments(executable: Path, profile: Path, *, headless: bool, pipe: bool, window: tuple[int, int],
               extra: list[str]) -> list[str]:
    args = [
        f"--user-data-dir={profile}",
        "--remote-debugging-pipe" if pipe else "--remote-debugging-port=0",
        "--no-first-run",
        "--no-default-browser-check",
        "--no-service-autorun",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-sync",
        "--disable-default-apps",
        "--disable-extensions",
        "--disable-component-extensions-with-background-pages",
        "--disable-breakpad",
        "--disable-client-side-phishing-detection",
        "--disable-hang-monitor",
        "--disable-popup-blocking",
        "--disable-prompt-on-repost",
        "--disable-search-engine-choice-screen",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-ipc-flooding-protection",
        "--disable-features=Translate,MediaRouter,OptimizationHints,AutofillServerCommunication,"
        "PasswordLeakDetection,CalculateNativeWinOcclusion,InterestFeedContentSuggestions",
        "--deny-permission-prompts",
        "--password-store=basic",
        "--use-mock-keychain",
        "--mute-audio",
        f"--window-size={window[0]},{window[1]}",
    ]
    if not pipe:
        args.append("--remote-debugging-address=127.0.0.1")
    if headless:
        args += ["--headless" if _is_headless_shell(executable) else "--headless=new", "--hide-scrollbars"]
    if sys.platform.startswith("linux"):
        args.append("--disable-dev-shm-usage")
        if hasattr(os, "geteuid") and os.geteuid() == 0 or os.environ.get("NEWSWIRE_BROWSER_NO_SANDBOX") == "1":
            args.append("--no-sandbox")  # Chromium refuses to start as root otherwise (containers)
    return args + list(extra) + ["about:blank"]


def _write_preferences(profile: Path) -> None:
    """A fresh profile that does not offer to save passwords or restore a "crashed" session."""
    prefs = {
        "credentials_enable_service": False,
        "profile": {"password_manager_enabled": False, "password_manager_leak_detection": False,
                    "exit_type": "Normal", "exited_cleanly": True},
        "autofill": {"profile_enabled": False, "credit_card_enabled": False},
        "translate": {"enabled": False},
        "browser": {"check_default_browser": False},
        "session": {"restore_on_startup": 5},
    }
    default = profile / "Default"
    default.mkdir(parents=True, exist_ok=True)
    (default / "Preferences").write_text(json.dumps(prefs), encoding="utf-8")


_live: "weakref.WeakSet[ChromeProcess]" = weakref.WeakSet()
_live_guard = threading.Lock()


@atexit.register
def _close_all() -> None:  # pragma: no cover - interpreter shutdown
    with _live_guard:
        processes = list(_live)
    for proc in processes:
        try:
            proc.close()
        except Exception:  # noqa: BLE001
            pass


class ChromeProcess:
    """A running browser with its connection. `close()` stops it and deletes its profile folder."""

    def __init__(self, executable: Path, *, headless: bool, transport: str = "auto",
                 window: tuple[int, int] = (1280, 900), extra_args: list[str] | None = None):
        self.executable = executable
        self.headless = headless
        self.connection: Connection | None = None
        self.version = ""
        self._popen: subprocess.Popen | None = None
        self._pid: int | None = None
        self._exit: int | None = None
        self._closed = False
        self._close_lock = threading.Lock()
        if transport == "auto":
            transport = "pipe" if os.name == "posix" and hasattr(os, "posix_spawn") else "websocket"
        if transport not in ("pipe", "websocket"):
            raise ValueError(f"unknown transport {transport!r}")
        self.transport = transport
        self._profile = Path(tempfile.mkdtemp(prefix="newswire-robot-"))
        self._log_path = self._profile / "browser.log"
        try:
            _write_preferences(self._profile)
            args = _arguments(executable, self._profile, headless=headless, pipe=transport == "pipe",
                              window=window, extra=extra_args or [])
            if transport == "pipe":
                self._start_with_pipe(args)
            else:
                self._start_with_port(args)
            self.version = self.connection.send("Browser.getVersion", timeout=STARTUP_SECONDS).get("product", "")
        except BaseException as exc:
            detail = self._log_tail()
            self.close()
            if isinstance(exc, (RobotError, OSError)):
                raise BrowserLaunchError(self._explain(str(exc), detail)) from exc
            raise
        with _live_guard:
            _live.add(self)

    # -- starting ---------------------------------------------------------------------------------

    def _start_with_pipe(self, args: list[str]) -> None:
        """Chromium reads commands on descriptor 3 and writes replies on descriptor 4."""
        to_browser_r, to_browser_w = os.pipe()
        from_browser_r, from_browser_w = os.pipe()
        fds = [to_browser_r, to_browser_w, from_browser_r, from_browser_w]
        try:
            # Move every end above 4 so the child's descriptors 3 and 4 can be assigned in any order.
            import fcntl

            moved = []
            for fd in fds:
                moved.append(fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 10))
                os.close(fd)
            fds = moved
            child_in, parent_out, parent_in, child_out = fds
            null = os.devnull
            actions = [
                (os.POSIX_SPAWN_OPEN, 0, null, os.O_RDONLY, 0),
                (os.POSIX_SPAWN_OPEN, 1, str(self._log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600),
                (os.POSIX_SPAWN_DUP2, 1, 2),
                (os.POSIX_SPAWN_DUP2, child_in, 3),
                (os.POSIX_SPAWN_DUP2, child_out, 4),
            ]
            self._pid = os.posix_spawn(str(self.executable), [str(self.executable), *args], dict(os.environ),
                                       file_actions=actions, setpgroup=0)
        except BaseException:
            for fd in fds:
                try:
                    os.close(fd)
                except OSError:
                    pass
            raise
        os.close(child_in)
        os.close(child_out)
        self.connection = Connection(PipeTransport(parent_in, parent_out))

    def _start_with_port(self, args: list[str]) -> None:
        log_file = open(self._log_path, "ab")  # noqa: SIM115 - handed to the child
        try:
            kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": log_file, "stderr": subprocess.STDOUT}
            if os.name == "posix":
                kwargs["start_new_session"] = True
            else:
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            self._popen = subprocess.Popen([str(self.executable), *args], **kwargs)  # noqa: S603
        finally:
            log_file.close()
        self._pid = self._popen.pid
        port_file = self._profile / "DevToolsActivePort"
        deadline = time.monotonic() + STARTUP_SECONDS
        while True:
            if port_file.is_file():
                lines = port_file.read_text(encoding="utf-8", errors="replace").split("\n")
                if len(lines) >= 2 and lines[0].strip().isdigit() and lines[1].strip():
                    port, path = int(lines[0]), lines[1].strip()
                    break
            if not self.alive:
                raise BrowserLaunchError("The browser stopped while starting.")
            if time.monotonic() > deadline:
                raise BrowserLaunchError("The browser did not open its control port in time.")
            time.sleep(0.05)
        self.connection = Connection(WebSocketTransport("127.0.0.1", port, path))

    # -- state ------------------------------------------------------------------------------------

    @property
    def alive(self) -> bool:
        if self._pid is None or self._exit is not None:
            return False
        if self._popen is not None:
            code = self._popen.poll()
            if code is not None:
                self._exit = code
            return code is None
        try:
            pid, status = os.waitpid(self._pid, os.WNOHANG)
        except ChildProcessError:
            self._exit = -1
            return False
        if pid == 0:
            return True
        self._exit = os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else status
        return False

    def _log_tail(self) -> str:
        try:
            lines = [ln.strip() for ln in self._log_path.read_text(encoding="utf-8", errors="replace").splitlines()]
        except OSError:
            return ""
        lines = [ln for ln in lines if ln]
        return " | ".join(lines[-3:])[:400]

    def _explain(self, error: str, detail: str) -> str:
        low = detail.lower()
        if not self.headless and any(k in low for k in ("display", "x server", "ozone", "wayland")):
            return "The browser cannot open a window on this computer (no screen)."
        if "shared libraries" in low:
            return f"The browser at {self.executable} is missing system libraries: {detail}"
        if "sandbox" in low and "root" in low:
            return ("The browser will not run as the root user without --no-sandbox. Set "
                    "NEWSWIRE_BROWSER_NO_SANDBOX=1 or run NEWSWIRE as an ordinary user.")
        return f"The browser at {self.executable} did not start ({error})" + (f": {detail}" if detail else "")

    # -- stopping ---------------------------------------------------------------------------------

    def _wait(self, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while self.alive:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.02)
        return True

    def _signal(self, sig: int) -> None:
        if self._pid is None or not self.alive:
            return
        try:
            if self._popen is not None and os.name != "posix":
                self._popen.terminate() if sig == signal.SIGTERM else self._popen.kill()
            else:
                os.kill(self._pid, sig)
        except OSError:
            pass

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        conn = self.connection
        if conn is not None and not conn.closed and self.alive:
            conn.post("Browser.close")
            self._wait(3.0)
        if self.alive:
            self._signal(signal.SIGTERM)
            if not self._wait(3.0):
                self._signal(getattr(signal, "SIGKILL", signal.SIGTERM))
                self._wait(3.0)
        if os.name == "posix" and self._pid is not None:
            try:  # helpers that outlived the main process (same process group)
                os.killpg(self._pid, signal.SIGKILL)
            except OSError:
                pass
        if conn is not None:
            conn.close()
        for attempt in range(5):
            shutil.rmtree(self._profile, ignore_errors=True)
            if not self._profile.exists():
                break
            time.sleep(0.2 * (attempt + 1))
        with _live_guard:
            _live.discard(self)

    def __del__(self):  # pragma: no cover - last resort
        try:
            if not self._closed:
                self.close()
        except Exception:  # noqa: BLE001
            pass
