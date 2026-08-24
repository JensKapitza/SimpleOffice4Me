"""Bounded application logging with mandatory secret redaction."""

import logging
import re
from logging.config import dictConfig


REDACTIONS = (
    (re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+"), "[REDACTED-AUTH]"),
    (re.compile(r"(?i)(?:authorization|cookie|set-cookie|password|passwd|secret|token|api[_-]?key)\s*[:=]\s*[^\s,;]+"), "[REDACTED]"),
    (re.compile(r"(?i)([?&](?:code|token|password|secret|key)=)[^&\s]+"), r"\1[REDACTED]"),
)


def redact(value: object) -> str:
    text = str(value)
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def initlogging():
    dictConfig({
        "version": 1, "disable_existing_loggers": False,
        "formatters": {"default": {"format": "[%(asctime)s] %(levelname)s in %(module)s: %(message)s"}},
        "filters": {"redact": {"()": "app.applogging.SecretRedactionFilter"}},
        "handlers": {
            "wsgi": {"class": "logging.StreamHandler", "stream": "ext://flask.logging.wsgi_errors_stream", "formatter": "default", "filters": ["redact"]},
            "file": {"class": "logging.handlers.RotatingFileHandler", "formatter": "default", "filters": ["redact"], "filename": "logconfig.log", "maxBytes": 1024 * 1024, "backupCount": 3, "encoding": "utf-8"},
        },
        "root": {"level": "ERROR", "handlers": ["wsgi", "file"]},
    })
