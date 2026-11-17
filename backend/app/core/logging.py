"""Structured JSON logging with secret redaction, to stdout and a rotating file."""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
from datetime import datetime, timezone

from app.core.config import get_settings

_REDACT_KEYS = re.compile(
    r"(?i)(password|passwd|secret|token|authorization|cookie|api[_-]?key|app[_-]?password)"
    r"(\"?\s*[:=]\s*\"?)([^\s\",;&]+)"
)
_BEARER = re.compile(r"(?i)(bearer|basic)\s+[a-z0-9._~+/=-]+")


def redact(text: str) -> str:
    text = _REDACT_KEYS.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
    return _BEARER.sub(lambda m: f"{m.group(1)} [REDACTED]", text)


class RedactingJSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        for key in ("source_id", "job", "event", "duration_ms", "status"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


class RedactingTextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


_configured = False


def configure_logging(process: str = "api") -> None:
    global _configured
    if _configured:
        return
    settings = get_settings()
    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())
    for h in list(root.handlers):
        root.removeHandler(h)

    stream = logging.StreamHandler()
    stream.setFormatter(
        RedactingJSONFormatter()
        if settings.log_json
        else RedactingTextFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(stream)

    file_handler = logging.handlers.RotatingFileHandler(
        settings.logs_dir / f"newswire-{process}.log", maxBytes=5_000_000, backupCount=5
    )
    file_handler.setFormatter(RedactingJSONFormatter())
    root.addHandler(file_handler)

    for noisy in ("httpx", "httpcore", "apscheduler.executors.default", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True
