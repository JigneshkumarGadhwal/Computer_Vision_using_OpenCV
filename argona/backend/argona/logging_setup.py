"""Structured logging with secret redaction."""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone

_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password|private[_-]?key|signature|authorization)(['\"]?\s*[:=]\s*['\"]?)([^\s'\",}]+)"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\b0x[a-fA-F0-9]{64}\b"),  # EVM private keys
    re.compile(r"(?i)bot\d{6,}:[A-Za-z0-9_-]{20,}"),  # telegram bot tokens in URLs
]


def redact(text: str) -> str:
    out = text
    for p in _SECRET_PATTERNS:
        if p.groups >= 3:
            out = p.sub(lambda m: f"{m.group(1)}{m.group(2)}***", out)
        else:
            out = p.sub("***REDACTED***", out)
    return out


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        extra = getattr(record, "ctx", None)
        if extra:
            payload["ctx"] = json.loads(redact(json.dumps(extra, default=str)))
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


class PlainFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(level: str = "INFO", json_logs: bool = True) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter() if json_logs else PlainFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(h)
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "sqlalchemy.engine", "aiosqlite", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
