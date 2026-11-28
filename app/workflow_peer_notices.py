"""Single-purpose, opt-in workflow notice delivery between directly paired instances."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import urllib.error
import urllib.parse
import urllib.request

import click
from flask import Blueprint, current_app, jsonify, request
from flask.cli import with_appcontext

from .access_control import has_feature
from .db import get_db
from .document_copy_events import EVENT_NAME, JOB_EVENT_NAME
from .federation_core import sanitize_peer_id
from .federation_local_identity import peer_id as local_identity
from .federation_peer_auth import authenticate, headers as signed_headers
from .federation_store import FederationStore
from .federation_trust_store import FederationTrustStore
from .file_lock import exclusive_file_lock
from .sqlite_utils import connect
from .v3_activity import ActivityStore
from .v3_automation_timing import action_window, received_window_status, timing_envelope, utc_instant
from .v3_capabilities import enabled

PATH = "/federation/v3/workflow-notices"
PEER_NOTICE = "automation.notice.peer_copy_completed"
MAX_WIRE_BYTES = 4096
ID = re.compile(r"[a-f0-9]{64}")
DEST = re.compile(r"[a-f0-9]{32}")
SOURCES = {EVENT_NAME: "browser.document_copy", JOB_EVENT_NAME: "replication.batch"}
bp = Blueprint("workflow_peer_notices", __name__)


class PeerNoticeGrants:
    """Grant ownership is local; the wire never supplies a local username."""
    def __init__(self, root):
        self.directory = Path(root).resolve() / ".simpleoffice-meta"
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not stat.S_ISDIR(self.directory.lstat().st_mode):
            raise OSError("private workflow grant directory required")
        self.path = self.directory / "workflow-notice-grants.sqlite3"
        try:
            descriptor = os.open(self.path, os.O_EXCL | os.O_CREAT | os.O_WRONLY, 0o600)
        except FileExistsError:
            info = self.path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (os.name == "posix" and info.st_mode & 0o077):
                raise OSError("private workflow grant file required") from None
        else:
            os.close(descriptor)
        with connect(self.path) as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS peer_notice_grant(
                   grant_id TEXT PRIMARY KEY, peer TEXT NOT NULL, username TEXT NOT NULL,
                   account_id INTEGER NOT NULL, auth_version INTEGER NOT NULL,
                   UNIQUE(peer, account_id))"""
            )

    def lock(self):
        return exclusive_file_lock(self.directory / ".workflow-notice-grants.lock")

    def grant(self, peer, account):
        with self.lock(), connect(self.path) as db:
            existing = db.execute(
                "SELECT grant_id, username, auth_version FROM peer_notice_grant WHERE peer=? AND account_id=?",
                (peer, account["id"]),
            ).fetchone()
            if existing and existing[1:] == (account["username"], account["auth_version"]):
                return existing[0]
            key = secrets.token_hex(16)
            db.execute(
                """INSERT INTO peer_notice_grant VALUES (?,?,?,?,?)
                   ON CONFLICT(peer, account_id) DO UPDATE SET
                   grant_id=excluded.grant_id,username=excluded.username,auth_version=excluded.auth_version""",
                (key, peer, account["username"], account["id"], account["auth_version"]),
            )
            return key

    def for_account(self, account):
        with connect(self.path) as db:
            return db.execute(
                "SELECT grant_id,peer FROM peer_notice_grant WHERE account_id=? AND auth_version=? ORDER BY peer",
                (account["id"], account["auth_version"]),
            ).fetchall()

    def revoke(self, grant, account):
        with self.lock(), connect(self.path) as db:
            db.execute("DELETE FROM peer_notice_grant WHERE grant_id=? AND account_id=?", (grant, account["id"]))

    def resolve(self, grant, peer):
        with connect(self.path) as db:
            row = db.execute(
                "SELECT username, account_id, auth_version FROM peer_notice_grant WHERE grant_id=? AND peer=?",
                (grant, peer),
            ).fetchone()
        if not row:
            return None
        active = get_db().execute(
            """SELECT username,id,is_admin,is_disabled FROM user WHERE id=? AND username=? COLLATE BINARY
               AND auth_version=? AND COALESCE(is_disabled,0)=0""",
            (row[1], row[0], row[2]),
        ).fetchone()
        return active["username"] if active and has_feature(active, "sync") else None


def feature_on(root):
    return all(enabled(key, root=root) for key in ("v3.federation", "v3.automation", "v3.activity"))


def permitted_peer(root, peer_id, direction):
    store = FederationStore(root)
    peer_id = sanitize_peer_id(peer_id)
    peer = store.get_peer(peer_id)
    trust = FederationTrustStore(root).get_trust(peer_id)
    if (not peer or not peer.get("enabled") or peer_id in store.banned_peer_ids()
            or not trust or trust.get("trust_level") not in {"NORMAL", "HIGH"}
            or trust.get("verification_state") not in {"VERIFIED_MUTUAL", "VERIFIED_IN_PERSON", "VERIFIED_ADMIN"}):
        raise PermissionError("direct verified peer required")
    classes = (peer.get("policy") or {}).get("data_classes", {})
    policy = classes.get("workflow_notices", {}) if isinstance(classes, dict) else {}
    if not isinstance(policy, dict) or policy.get(direction) is not True:
        raise PermissionError("workflow notice direction not enabled")
    return peer


def _now():
    return datetime.now(timezone.utc)


def _notice_window_status(window, now, *, uncertainty_seconds=0):
    status = received_window_status(window, now, uncertainty_seconds=uncertainty_seconds)
    if window["expires_at"] is None:
        raise ValueError("workflow notice needs a finite deadline")
    origin = utc_instant(window["requested_at"])
    deadline = utc_instant(window["expires_at"])
    elapsed = deadline - origin
    if elapsed <= timedelta(0) or elapsed > timedelta(hours=26):
        raise ValueError("workflow notice deadline is out of bounds")
    if elapsed > timedelta(minutes=15):
        _, midnight = action_window({"end_of_day": True}, origin, window["origin_timezone"])
        if deadline != midnight:
            raise ValueError("a long workflow notice deadline must be local midnight")
    return status


def receive_notice(root, peer, payload, *, clock=_now):
    if (type(payload) is not dict or set(payload) != {"id", "destination", "kind", "window"}
            or not isinstance(payload["id"], str) or not ID.fullmatch(payload["id"])
            or not isinstance(payload["destination"], str) or not DEST.fullmatch(payload["destination"])
            or payload["kind"] not in ("browser", "job")):
        raise ValueError("invalid workflow notice")
    timing = payload["window"]
    _notice_window_status(timing, clock(), uncertainty_seconds=1)
    grants = PeerNoticeGrants(root)
    destination = payload["destination"]
    with grants.lock():
        permitted_peer(root, peer, "receive")
        account = grants.resolve(destination, peer)
        if not account:
            raise PermissionError("workflow notice destination not granted")
        now = clock()
        status = received_window_status(timing, now, uncertainty_seconds=1)
        if status != "ready":
            return status
        activities = ActivityStore(root)
        key = hashlib.sha256(f"peer-copy-notice\0{peer}\0{destination}\0{payload['id']}".encode()).hexdigest()
        if activities.find_for_actor(key, account):
            return "duplicate"
        since = (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        if len(activities.list(actor=account, event_type=PEER_NOTICE, since=since, limit=30)) >= 30:
            return "recipient_busy"
        status = received_window_status(timing, clock(), uncertainty_seconds=1)
        if status != "ready":
            return status
        activities.record(PEER_NOTICE, account, event_id=key, source="workflow.peer_notice")
        return "delivered"


@bp.post(PATH)
def receive():
    root = current_app.config["DOCUMENT_ROOT"]
    if not feature_on(root):
        return jsonify(status="disabled"), 404
    if not request.is_secure:
        return jsonify(status="verified_https_required"), 403
    if not request.is_json or not request.content_length or request.content_length > MAX_WIRE_BYTES:
        return jsonify(status="invalid"), 400
    try:
        peer = authenticate(root, request)
    except (ValueError, TypeError):
        return jsonify(status="peer_authentication_required"), 401
    try:
        payload = request.get_json(silent=True)
        status = receive_notice(root, peer, payload, clock=_now)
    except PermissionError:
        return jsonify(status="denied"), 403
    except (ValueError, TypeError, KeyError, OverflowError):
        return jsonify(status="invalid"), 400
    return jsonify(status=status)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send_notice(root, peer_id, destination, event, *, window=None):
    if not feature_on(root) or not DEST.fullmatch(destination):
        raise PermissionError("workflow notices or destination not available")
    if SOURCES.get(event.event_type) != event.source:
        raise ValueError("a confirmed copy event is required")
    peer = permitted_peer(root, peer_id, "send")
    url = urllib.parse.urlsplit(peer["base_url"])
    if (url.scheme != "https" or not url.hostname or url.username is not None or url.password is not None
            or url.path not in {"", "/"} or url.query or url.fragment):
        raise ValueError("a configured HTTPS peer origin is required")
    token = FederationStore(root).peer_token(peer["peer_id"])
    if not token:
        raise PermissionError("peer credential missing")
    if window is None:
        window = timing_envelope({"within_seconds": 600}, event.occurred_at)
    status = _notice_window_status(window, _now())
    if utc_instant(window["requested_at"]) != utc_instant(event.occurred_at):
        raise ValueError("workflow notice needs the original event timestamp")
    if status != "ready":
        return status
    key = hashlib.sha256(f"copy\0{event.event_id}".encode()).hexdigest()
    payload = {"destination": destination, "id": key, "kind": "job" if event.event_type == JOB_EVENT_NAME else "browser",
               "window": window}
    body = json.dumps(payload, separators=(",", ":")).encode()
    url = urllib.parse.urlunsplit(("https", url.netloc, PATH, "", ""))
    req = urllib.request.Request(url, data=body, method="POST", headers={
        **signed_headers(local_identity(), token, "POST", PATH, body),
        "Content-Type": "application/json", "Accept": "application/json",
    })
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with client.open(req, timeout=8) as response:
            raw = response.read(MAX_WIRE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in {400, 401, 403, 404}:
            return "denied"
        raise ConnectionError("peer request failed") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ConnectionError("peer offline or HTTPS could not be verified") from exc
    if len(raw) > MAX_WIRE_BYTES:
        raise ValueError("invalid peer notice response")
    value = json.loads(raw)
    status = value.get("status") if isinstance(value, dict) else None
    if status not in {"delivered", "duplicate", "window_missed", "waiting_for_window", "recipient_busy"}:
        raise ValueError("invalid peer notice response")
    return status


@click.command("send-workflow-copy-notice")
@click.option("--peer", required=True, help="Already-paired verified peer ID.")
@click.option("--destination", required=True, help="Opaque destination provided by the recipient.")
@click.option("--actor", required=True, help="Audit actor of the already completed copy.")
@click.option("--event-id", required=True, help="ID of the already completed copy event.")
@with_appcontext
def send_command(peer, destination, actor, event_id):
    root = current_app.config["DOCUMENT_ROOT"]
    try:
        event = ActivityStore(root).find_for_actor(event_id, actor)
        if not event:
            raise ValueError("copy event not found")
        status = send_notice(root, peer, destination, event)
    except (ValueError, PermissionError, ConnectionError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(status)
    if status not in {"delivered", "duplicate"}:
        raise click.ClickException("notice not delivered")


@click.command("list-workflow-copy-events")
@click.option("--actor", required=True, help="Audit actor of the completed copy, e.g. system.")
@with_appcontext
def list_command(actor):
    root = current_app.config["DOCUMENT_ROOT"]
    if not feature_on(root):
        raise click.ClickException("workflow notices are disabled")
    activities = ActivityStore(root)
    rows = [event for event_type, source in SOURCES.items()
            for event in activities.list(actor=actor, event_type=event_type, limit=10)
            if event.source == source]
    for event in sorted(rows, key=lambda row: (row.occurred_at, row.event_id), reverse=True)[:10]:
        click.echo(f"{event.event_id} {event.event_type} {event.occurred_at}")
