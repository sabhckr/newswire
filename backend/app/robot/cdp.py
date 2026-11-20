"""The wire to the browser: Chrome DevTools Protocol over a private pipe or a local WebSocket.

Standard library only. Two transports carry the same JSON messages:

* `PipeTransport` (macOS, Linux): the browser's `--remote-debugging-pipe`. Messages are JSON
  terminated by a NUL byte on two inherited file descriptors. Nothing listens on the network,
  so no other program on the computer can attach to the browser.
* `WebSocketTransport` (Windows, where descriptor inheritance works differently): the browser's
  `--remote-debugging-port` on 127.0.0.1, spoken with a small RFC 6455 client.

`Connection` matches replies to requests and hands events to listeners. One reader thread per
connection; `send` may be called from any other thread.
"""

from __future__ import annotations

import base64
import hashlib
import itertools
import json
import logging
import os
import select
import socket
import struct
import threading
from typing import Any, Callable, Iterator

from app.robot.errors import BrowserClosed, ProtocolError, RobotTimeout

log = logging.getLogger(__name__)

Listener = Callable[[str, dict, "str | None"], None]


class PipeTransport:
    """NUL-separated JSON on a pair of pipe file descriptors (POSIX)."""

    def __init__(self, read_fd: int, write_fd: int):
        self._r = read_fd
        self._w = write_fd
        self._stop = threading.Event()
        self._write_lock = threading.Lock()
        self._closed = False

    def send(self, data: bytes) -> None:
        payload = memoryview(data + b"\0")
        with self._write_lock:
            if self._closed:
                raise BrowserClosed("The browser is closed.")
            try:
                while payload:
                    n = os.write(self._w, payload)
                    payload = payload[n:]
            except OSError as exc:
                raise BrowserClosed("The browser closed the connection.") from exc

    def messages(self) -> Iterator[bytes]:
        buf = bytearray()
        while not self._stop.is_set():
            try:
                ready, _, _ = select.select([self._r], [], [], 0.25)
                if not ready:
                    continue
                chunk = os.read(self._r, 1 << 16)
            except (OSError, ValueError):
                return
            if not chunk:
                return  # the browser exited
            buf += chunk
            while True:
                end = buf.find(b"\0")
                if end < 0:
                    break
                message = bytes(buf[:end])
                del buf[: end + 1]
                if message:
                    yield message

    def stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        self._stop.set()
        with self._write_lock:
            if self._closed:
                return
            self._closed = True
            for fd in (self._r, self._w):
                try:
                    os.close(fd)
                except OSError:
                    pass


class WebSocketTransport:
    """A minimal WebSocket client for ws://127.0.0.1:<port>/<path> (text frames, no extensions)."""

    GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def __init__(self, host: str, port: int, path: str, timeout: float = 10.0):
        self._stop = threading.Event()
        self._write_lock = threading.Lock()
        self._closed = False
        self._sock = socket.create_connection((host, port), timeout=timeout)
        try:
            self._handshake(host, port, path)
        except Exception:
            self._sock.close()
            raise
        self._sock.settimeout(0.25)

    def _handshake(self, host: str, port: int, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode()
        request = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self._sock.sendall(request.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise BrowserClosed("The browser closed the connection while it was being set up.")
            head += chunk
            if len(head) > 65536:
                raise BrowserClosed("The browser did not answer like a debugging port.")
        header_text, _, rest = head.partition(b"\r\n\r\n")
        lines = header_text.decode("latin-1").split("\r\n")
        headers = {k.strip().lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:])}
        expected = base64.b64encode(hashlib.sha1((key + self.GUID).encode()).digest()).decode()
        if " 101 " not in lines[0] + " " or headers.get("sec-websocket-accept") != expected:
            raise BrowserClosed(f"The browser refused the debugging connection ({lines[0]}).")
        self._pending = bytearray(rest)

    def send(self, data: bytes) -> None:
        self._send_frame(0x1, data)

    def _send_frame(self, opcode: int, data: bytes) -> None:
        n = len(data)
        head = bytearray([0x80 | opcode])
        if n < 126:
            head.append(0x80 | n)
        elif n < 1 << 16:
            head.append(0x80 | 126)
            head += struct.pack(">H", n)
        else:
            head.append(0x80 | 127)
            head += struct.pack(">Q", n)
        mask = os.urandom(4)
        if n:
            repeated = (mask * (n // 4 + 1))[:n]
            masked = (int.from_bytes(data, "big") ^ int.from_bytes(repeated, "big")).to_bytes(n, "big")
        else:
            masked = b""
        with self._write_lock:
            if self._closed:
                raise BrowserClosed("The browser is closed.")
            try:
                self._sock.settimeout(30)
                self._sock.sendall(bytes(head) + mask + masked)
            except OSError as exc:
                raise BrowserClosed("The browser closed the connection.") from exc
            finally:
                try:
                    self._sock.settimeout(0.25)
                except OSError:
                    pass

    def _read(self, n: int) -> bytes | None:
        """Exactly `n` bytes, or None when the connection ended or was stopped."""
        while len(self._pending) < n:
            if self._stop.is_set():
                return None
            try:
                chunk = self._sock.recv(max(1 << 16, n - len(self._pending)))
            except socket.timeout:
                continue
            except OSError:
                return None
            if not chunk:
                return None
            self._pending += chunk
        out = bytes(self._pending[:n])
        del self._pending[:n]
        return out

    def messages(self) -> Iterator[bytes]:
        parts: list[bytes] = []
        while True:
            head = self._read(2)
            if head is None:
                return
            fin, opcode, n = head[0] & 0x80, head[0] & 0x0F, head[1] & 0x7F
            masked = head[1] & 0x80
            if n == 126:
                ext = self._read(2)
                if ext is None:
                    return
                n = struct.unpack(">H", ext)[0]
            elif n == 127:
                ext = self._read(8)
                if ext is None:
                    return
                n = struct.unpack(">Q", ext)[0]
            mask = self._read(4) if masked else None
            if masked and mask is None:
                return
            payload = self._read(n) if n else b""
            if payload is None:
                return
            if mask and n:
                repeated = (mask * (n // 4 + 1))[:n]
                payload = (int.from_bytes(payload, "big") ^ int.from_bytes(repeated, "big")).to_bytes(n, "big")
            if opcode == 0x8:  # close
                return
            if opcode == 0x9:  # ping
                try:
                    self._send_frame(0xA, payload)
                except BrowserClosed:
                    return
                continue
            if opcode == 0xA:  # pong
                continue
            parts.append(payload)
            if fin:
                message = b"".join(parts)
                parts = []
                if message:
                    yield message

    def stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        self._stop.set()
        with self._write_lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._sock.close()
            except OSError:
                pass


class Pending:
    """A command that was sent; `wait` returns the browser's reply."""

    __slots__ = ("method", "_done", "_result", "_error")

    def __init__(self, method: str):
        self.method = method
        self._done = threading.Event()
        self._result: dict | None = None
        self._error: Exception | None = None

    def resolve(self, result: dict | None = None, error: Exception | None = None) -> None:
        self._result, self._error = result, error
        self._done.set()

    @property
    def done(self) -> bool:
        return self._done.is_set()

    def wait(self, timeout: float = 30.0) -> dict:
        if not self._done.wait(timeout):
            raise RobotTimeout(f"The browser did not answer {self.method} within {timeout:g} seconds.")
        if self._error is not None:
            raise self._error
        return self._result or {}


class Connection:
    def __init__(self, transport: Any):
        self._transport = transport
        self._ids = itertools.count(1)
        self._pending: dict[int, Pending] = {}
        self._lock = threading.Lock()
        self._listeners: list[Listener] = []
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._read_loop, name="robot-cdp", daemon=True)
        self._thread.start()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def subscribe(self, listener: Listener) -> None:
        """`listener(method, params, session_id)` runs on the reader thread: it must not block or
        call `send` (use `post`)."""
        self._listeners.append(listener)

    def post(self, method: str, params: dict | None = None, session: str | None = None) -> Pending:
        """Send a command without waiting for the answer."""
        pending = Pending(method)
        msg_id = next(self._ids)
        message: dict[str, Any] = {"id": msg_id, "method": method}
        if params:
            message["params"] = params
        if session:
            message["sessionId"] = session
        with self._lock:
            if self._closed.is_set():
                pending.resolve(error=BrowserClosed("The browser is closed."))
                return pending
            self._pending[msg_id] = pending
        try:
            self._transport.send(json.dumps(message, separators=(",", ":")).encode())
        except BrowserClosed as exc:
            with self._lock:
                self._pending.pop(msg_id, None)
            pending.resolve(error=exc)
        return pending

    def send(self, method: str, params: dict | None = None, session: str | None = None,
             timeout: float = 30.0) -> dict:
        if threading.current_thread() is self._thread:
            raise RuntimeError("Connection.send would wait for itself on the reader thread; use post()")
        return self.post(method, params, session).wait(timeout)

    def _read_loop(self) -> None:
        try:
            for raw in self._transport.messages():
                try:
                    msg = json.loads(raw)
                except ValueError:
                    log.warning("robot: unreadable message from the browser (%d bytes)", len(raw))
                    continue
                if "id" in msg:
                    with self._lock:
                        pending = self._pending.pop(msg["id"], None)
                    if pending is None:
                        continue
                    if "error" in msg:
                        err = msg["error"] or {}
                        pending.resolve(error=ProtocolError(pending.method, str(err.get("message") or "error"),
                                                            err.get("code")))
                    else:
                        pending.resolve(msg.get("result") or {})
                elif "method" in msg:
                    for listener in list(self._listeners):
                        try:
                            listener(msg["method"], msg.get("params") or {}, msg.get("sessionId"))
                        except Exception:  # noqa: BLE001 - one bad handler must not stop the reader
                            log.exception("robot: handler for %s failed", msg["method"])
        except Exception:  # noqa: BLE001
            log.exception("robot: the browser connection failed")
        finally:
            self._fail_all()

    def _fail_all(self) -> None:
        with self._lock:
            self._closed.set()
            waiting = list(self._pending.values())
            self._pending.clear()
        for pending in waiting:
            pending.resolve(error=BrowserClosed("The browser was closed."))

    def close(self) -> None:
        self._transport.stop()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=3)
        self._transport.close()
        self._fail_all()
