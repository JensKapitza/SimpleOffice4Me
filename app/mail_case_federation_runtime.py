"""Background retry worker for durable mail-case federation events."""
from __future__ import annotations

import os
import threading
import time

from .mail_case_federation import retry_due_mail_case_events

_LOCK = threading.Lock()
_STARTED = False


def _enabled() -> bool:
    return os.environ.get("SIMPLEOFFICE_MAIL_CASE_FEDERATION_WORKER", "1").strip().casefold() not in {
        "0", "false", "no", "off",
    }


def _worker(app) -> None:
    local_peer_id = os.environ.get("SIMPLEOFFICE_FEDERATION_PEER_ID", "simpleoffice-local")
    while True:
        with app.app_context():
            try:
                result = retry_due_mail_case_events(
                    app.config["DOCUMENT_ROOT"], local_peer_id, limit=5,
                )
                if result["sent"] or result["failed"]:
                    app.logger.info(
                        "mail_case_federation_retry sent=%s failed=%s queued=%s",
                        result["sent"], result["failed"], result["queued"],
                    )
            except Exception as exc:
                app.logger.warning("mail_case_federation_worker_failed error=%s", type(exc).__name__)
        time.sleep(15)


def _start(app) -> None:
    global _STARTED
    if app.testing or not _enabled():
        return
    with _LOCK:
        if _STARTED:
            return
        _STARTED = True
        threading.Thread(
            target=_worker, args=(app,), daemon=True,
            name="mail-case-federation-retry",
        ).start()


def _start_from_request() -> None:
    from flask import current_app

    _start(current_app._get_current_object())


def init_app(app) -> None:
    app.before_request(_start_from_request)
