"""Background retention maintenance for PrinterShare payloads.

TTL is a storage contract, so expiry must not depend on somebody opening the
PrinterShare page.  This module starts one lightweight daemon loop per Python
process and purges expired encrypted payloads independently of web traffic.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any

from .printershare_store import PrinterShareStore


_LOG = logging.getLogger(__name__)
_STARTED = False
_START_LOCK = threading.Lock()
_DEFAULT_INTERVAL_SECONDS = 30
_MIN_INTERVAL_SECONDS = 5
_MAX_INTERVAL_SECONDS = 3600


def _interval_seconds() -> int:
    raw = os.environ.get("SIMPLEOFFICE_PRINTERSHARE_PURGE_INTERVAL_SECONDS", str(_DEFAULT_INTERVAL_SECONDS))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = _DEFAULT_INTERVAL_SECONDS
    return max(_MIN_INTERVAL_SECONDS, min(value, _MAX_INTERVAL_SECONDS))


def purge_once(root: str, secret_key: object) -> int:
    """Delete every expired TTL payload that is still present."""
    return PrinterShareStore(root, secret_key).purge_expired()


def _purge_loop(root: str, secret_key: object, stop_event: threading.Event, interval: int) -> None:
    # Purge once immediately on process startup, then independently of requests.
    while not stop_event.is_set():
        try:
            removed = purge_once(root, secret_key)
            if removed:
                _LOG.info("printershare_retention_purge removed=%s", removed)
        except Exception:
            # A transient filesystem/SQLite failure must not kill future cleanup
            # attempts. The next interval retries and the error remains visible.
            _LOG.exception("printershare_retention_purge_failed")
        stop_event.wait(interval)


def init_app(app: Any) -> None:
    """Start the independent TTL cleanup loop for this application process."""
    global _STARTED
    if app.testing and not app.config.get("PRINTERSHARE_RETENTION_WORKER_IN_TESTS", False):
        return
    with _START_LOCK:
        if _STARTED:
            return
        _STARTED = True
        stop_event = threading.Event()
        app.extensions["printershare_retention_stop_event"] = stop_event
        worker = threading.Thread(
            target=_purge_loop,
            args=(
                str(app.config["DOCUMENT_ROOT"]),
                app.config["SECRET_KEY"],
                stop_event,
                _interval_seconds(),
            ),
            name="printershare-retention",
            daemon=True,
        )
        app.extensions["printershare_retention_worker"] = worker
        worker.start()
