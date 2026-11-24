"""Loglama: dönen dosya + konsol, gizli değer maskeleme.

``RedactingFilter`` hem ayarlardaki gerçek anahtar değerlerini hem de bilinen
anahtar biçimlerini (Telegram bot token, Anthropic/Google anahtarları, Bearer
token) log kaydına yazılmadan önce maskeler.
"""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

MASK = "***REDACTED***"

_PATTERNS = [
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),          # Telegram bot token
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),                 # Anthropic
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),                    # Google API key
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]{12,}=*"),   # Bearer token
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password)\s*[=:]\s*)[^\s&,;'\"]{6,}"),
]


def redact(text: str, secrets: list[str] | tuple[str, ...] = ()) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, MASK)
    for p in _PATTERNS:
        if p.groups:
            text = p.sub(lambda m: m.group(1) + MASK, text)
        else:
            text = p.sub(MASK, text)
    return text


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: list[str] | None = None):
        super().__init__()
        self.secrets = list(secrets or [])

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        record.msg = redact(msg, self.secrets)
        record.args = None
        if record.exc_text:
            record.exc_text = redact(record.exc_text, self.secrets)
        return True


_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(level: str = "INFO", log_dir: Path | None = None, secrets: list[str] | None = None) -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    for h in list(root.handlers):
        root.removeHandler(h)

    redactor = RedactingFilter(secrets)
    fmt = logging.Formatter(_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.addFilter(redactor)
    root.addHandler(console)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        for name, lvl in (("sepetfy.log", logging.DEBUG), ("errors.log", logging.ERROR)):
            fh = RotatingFileHandler(log_dir / name, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
            fh.setLevel(lvl)
            fh.setFormatter(fmt)
            fh.addFilter(redactor)
            root.addHandler(fh)

    # Harici kütüphanelerin istek URL'lerini (token içerebilir) DEBUG'da yazmasını engelle
    for noisy in ("httpx", "httpcore", "urllib3", "apscheduler.executors"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
