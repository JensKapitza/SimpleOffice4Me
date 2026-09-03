"""Bounded application logging with mandatory secret redaction."""

import logging
import re
from logging.config import dictConfig


REDACTIONS = (
    # HTTP Authorization values.
    (re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+"), "[REDACTED-AUTH]"),
    # key=value / Header: value / environment-style values.
    (re.compile(r"(?i)(?:authorization|cookie|set-cookie|password|passwd|secret|token|api[_-]?key|client[_-]?secret|refresh[_-]?token|access[_-]?token)\s*[:=]\s*[^\s,;]+"), "[REDACTED]"),
    # Query-string credentials.
    (re.compile(r"(?i)([?&](?:code|token|password|secret|key|api_key|access_token|refresh_token)=)[^&\s]+"), r"\1[REDACTED]"),
    # JSON-ish credential fields emitted by exception/debug representations.
    (re.compile(r"(?i)([\"'](?:password|passwd|secret|token|api[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token)[\"']\s*:\s*[\"'])[^\"']*([\"'])"), r"\1[REDACTED]\2"),
    # user:password@host URLs. Preserve the username for diagnostics.
    (re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^\s/@:]+:)[^\s/@]+(@)"), r"\1[REDACTED]\2"),
)


def redact(value: object) -> str:
    text = str(value)
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    # Prevent a single accidental payload/trace line from exploding log files.
    return text[:32_000]


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
