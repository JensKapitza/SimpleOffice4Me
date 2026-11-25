"""Run the Google pull scheduler outside the HTTP request lifecycle."""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import click
from flask import current_app
from flask.cli import with_appcontext

from .access_control import has_feature
from .db import get_db
from .file_lock import exclusive_file_lock
from .google_calendar_automatic import POLL_SECONDS
from .google_calendar_sync import GoogleCalendarSync

MAX_ATTEMPTS_PER_SCAN = 4
_LOG = logging.getLogger(__name__)


def _access(actor: str, user_id: int) -> bool:
    user = get_db().execute(
        "SELECT id, is_admin, is_disabled FROM user WHERE id=? AND username=?", (user_id, actor),
    ).fetchone()
    return has_feature(user, "calendar")


def run_google_calendar_once(sync: GoogleCalendarSync, *, stop=None) -> int:
    candidates = [(state["automatic"].get("due", 0), actor) for actor, state in sync._state().items()
                  if isinstance(state, dict) and state.get("automatic")]
    if not candidates:
        return 0

    def access(actor: str, user_id: int) -> bool:
        return not (stop and stop.is_set()) and _access(actor, user_id)

    attempted = 0
    with exclusive_file_lock(sync.state_path.with_name(".google-calendar-worker.lock"), blocking=False) as acquired:
        if not acquired:
            return 0
        for _, actor in sorted(candidates):
            if attempted >= MAX_ATTEMPTS_PER_SCAN or (stop and stop.is_set()):
                break
            if sync.automatic.run_due(actor, access):
                attempted += 1
    return attempted


def _loop(app, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            with app.app_context():
                run_google_calendar_once(GoogleCalendarSync(app.config["DOCUMENT_ROOT"]), stop=stop)
        except Exception as exc:
            # Never emit OAuth requests, provider payloads or exception strings.
            _LOG.warning("Google Calendar scheduler scan failed (%s)", type(exc).__name__)
        stop.wait(POLL_SECONDS)


@contextmanager
def google_calendar_background(app) -> Iterator[None]:
    """Called by supported server entry points, never on app import/request."""
    stop = threading.Event()
    thread = threading.Thread(target=_loop, args=(app, stop), name="google-calendar-pull", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2)


@click.command("google-calendar-worker")
@click.option("--once", is_flag=True, help="Run one bounded scheduler scan and exit.")
@with_appcontext
def google_calendar_worker_command(once: bool) -> None:
    """Run the scheduler for a custom WSGI deployment."""
    app = current_app._get_current_object()
    if once:
        click.echo(f"attempted={run_google_calendar_once(GoogleCalendarSync(app.config['DOCUMENT_ROOT']))}")
    else:
        try:
            _loop(app, threading.Event())
        except KeyboardInterrupt:
            return
