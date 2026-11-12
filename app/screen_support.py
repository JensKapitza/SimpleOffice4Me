"""Consent-bound application support, durable notices and ephemeral input commands."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import time

from flask import abort, current_app, jsonify, request
from .auth import login_required

_MAX_EVENTS = 500
_MAX_COMMANDS = 128
_RECEIVER_TIMEOUT = 15
_runtime: dict[str, dict] = {}


def _screen():
    from . import screen_share
    return screen_share


def _path(session_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", session_id):
        abort(404)
    return Path(current_app.config["DOCUMENT_ROOT"]) / ".simpleoffice-meta" / "screen-support" / (session_id + ".json")


def _save(session_id, record):
    path = _path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".support-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(record, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _record(session_id, live):
    path = _path(session_id)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    if live is None:
        abort(404)
    return {"schema": 1, "owner": live["owner"], "receiver": "", "permission": "none", "generation": "", "closed": False, "sequence": 0, "events": []}


def _event(record, kind, actor, **detail):
    if len(record["events"]) >= _MAX_EVENTS:
        if kind not in {"revoke", "deny", "leave", "ended"}:
            abort(409, description="Support-Verlauf voll. Neue Sitzung starten.")
        record["closed"] = True
        _revoke(record)
    record["sequence"] += 1
    event = {"seq": record["sequence"], "type": kind, "actor": actor, "at": time.time(), "from": detail.pop("role", "sender"), **detail}
    record["events"].append(event)
    return event


def _revoke(record):
    record["permission"] = "none"
    record["generation"] = ""


def _session_ended(session_id, live=None):
    """Called under the screen-session lock on stop and expiry."""
    if not current_app.config.get("DOCUMENT_ROOT") or not _path(session_id).exists():
        _runtime.pop(session_id, None)
        return
    record = _record(session_id, live)
    if not record["closed"]:
        _revoke(record)
        record["closed"] = True
        if len(record["events"]) < _MAX_EVENTS:
            _event(record, "ended", record["owner"])
        _save(session_id, record)
    _runtime.pop(session_id, None)


def session_ended(session_id, live=None):
    # Ending capture must remain possible when its history disk is unavailable.
    try:
        _session_ended(session_id, live)
    except (OSError, ValueError):
        current_app.logger.exception("Screen support end could not be persisted")
    finally:
        _runtime.pop(session_id, None)


def _authorize(record, live, role, claim=False):
    screen = _screen()
    actor = screen._actor_id()
    if role == "sender":
        if actor != record["owner"]:
            abort(403)
    elif role == "receiver":
        code = request.headers.get("X-Screen-Code", "").strip().upper()
        if live is None or not screen._authorized(live, role, code):
            abort(403)
        if record["receiver"] and record["receiver"] != actor:
            abort(403)
        if not record["receiver"]:
            if not claim:
                abort(403)
            record["receiver"] = actor
    else:
        abort(400)
    return actor


def _cursor(name):
    try:
        return max(0, int(request.args.get(name, 0)))
    except (ValueError, TypeError):
        abort(400)


def _fields(body):
    epoch, fields = body.get("epoch"), body.get("fields")
    if not isinstance(epoch, str) or not 1 <= len(epoch) <= 100 or not isinstance(fields, list) or len(fields) > 200:
        abort(400)
    result = []
    seen = set()
    for item in fields:
        if not isinstance(item, dict):
            abort(400)
        identifier, label, kind = item.get("id"), item.get("label"), item.get("type")
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identifier) or identifier in seen:
            abort(400)
        if not isinstance(label, str) or len(label) > 160 or kind not in {"text", "select", "checkbox"}:
            abort(400)
        seen.add(identifier)
        clean = {"id": identifier, "label": label, "type": kind}
        if kind == "select":
            options = item.get("options")
            if not isinstance(options, list) or len(options) > 100:
                abort(400)
            clean["options"] = []
            for option in options:
                if not isinstance(option, dict) or any(not isinstance(option.get(key), str) or len(option[key]) > 200 for key in ("value", "label")):
                    abort(400)
                clean["options"].append({"value": option["value"], "label": option["label"]})
        result.append(clean)
    return epoch, result


def _input(record, runtime, body):
    if record["permission"] != "granted" or not record["generation"] or body.get("generation") != record["generation"]:
        abort(403)
    if body.get("epoch") != runtime["epoch"]:
        abort(409)
    field = next((field for field in runtime["fields"] if field["id"] == body.get("field_id")), None)
    if field is None:
        abort(400)
    value = body.get("value")
    if field["type"] == "checkbox":
        if type(value) is not bool:
            abort(400)
    elif not isinstance(value, str) or len(value) > 4000:
        abort(400)
    if field["type"] == "select" and value not in [option["value"] for option in field["options"]]:
        abort(400)
    if len(runtime["commands"]) >= _MAX_COMMANDS:
        abort(409)
    runtime["command_sequence"] += 1
    runtime["commands"].append({"seq": runtime["command_sequence"], "epoch": runtime["epoch"], "field_id": field["id"], "value": value, "generation": record["generation"]})


def _mutate(record, runtime, body, role, actor):
    kind = body.get("type")
    if kind == "fields":
        if role != "sender":
            abort(403)
        epoch, fields = _fields(body)
        previous = {field["id"]: field for field in runtime["fields"]}
        current = {field["id"]: field for field in fields}
        pending, rejected = [], []
        for command in runtime["commands"]:
            identifier = command["field_id"]
            if epoch == runtime["epoch"] and previous.get(identifier) == current.get(identifier) and identifier in current:
                pending.append(command)
            else:
                rejected.append(command)
        for command in rejected:
            if not any(event["type"] == "input-result" and event.get("command_seq") == command["seq"] for event in record["events"]):
                _event(record, "input-result", actor, role=role, command_seq=command["seq"], outcome="rejected", reason="field_changed")
        runtime.update(epoch=epoch, fields=fields, commands=pending)
        return bool(rejected)
    if kind == "input":
        if role != "receiver":
            abort(403)
        _input(record, runtime, body)
        return False
    if kind in {"grant", "deny", "revoke"} and role != "sender":
        abort(403)
    if kind in {"request", "join", "leave"} and role != "receiver":
        abort(403)
    if kind in {"request", "grant", "notice"} and len(record["events"]) >= _MAX_EVENTS - 2:
        abort(409)
    if kind == "request":
        if record["permission"] != "none":
            abort(409)
        record["permission"] = "requested"
    elif kind == "grant":
        if record["permission"] != "requested" or not record["receiver"]:
            abort(409)
        record["permission"] = "granted"
        record["generation"] = secrets.token_urlsafe(18)
    elif kind in {"deny", "revoke", "join", "leave"}:
        _revoke(record)
        runtime["commands"].clear()
    elif kind == "notice":
        text = body.get("text")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 1000:
            abort(400)
        _event(record, kind, actor, role=role, text=text.strip())
        return True
    elif kind == "input-result":
        if role != "sender" or body.get("outcome") not in {"applied", "rejected"}:
            abort(403)
        seq = body.get("command_seq")
        if type(seq) is not int or not any(command["seq"] == seq for command in runtime["commands"]):
            abort(400)
        if any(event["type"] == kind and event.get("command_seq") == seq for event in record["events"]):
            return False
        _event(record, kind, actor, role=role, command_seq=seq, outcome=body["outcome"])
        return True
    elif kind == "ack":
        seq = body.get("event_seq")
        if type(seq) is not int or not any(event["seq"] == seq and event["type"] == "notice" and event.get("from") != role for event in record["events"]):
            abort(400)
        if any(event["type"] == "ack" and event.get("event_seq") == seq and event.get("from") == role for event in record["events"]):
            return False
        _event(record, kind, actor, role=role, event_seq=seq)
        return True
    else:
        abort(400)
    _event(record, kind, actor, role=role)
    return True


def _support_request(session_id):
    screen = _screen()
    body = screen._signal_body()[0] if request.method == "POST" else {}
    role = body.get("role") if request.method == "POST" else request.args.get("role")
    with screen._sessions_lock:
        screen._purge_sessions()
        live = screen._sessions.get(session_id)
        if live is not None and live.get("closed"):
            live = None
        record = _record(session_id, live)
        original_receiver = record["receiver"]
        actor = _authorize(record, live, role, claim=request.method == "GET" or body.get("type") == "join")
        if live is None and not record["closed"]:
            session_ended(session_id)
            record = _record(session_id, None)
        runtime = {"epoch": "", "fields": [], "commands": [], "command_sequence": 0} if record["closed"] else _runtime.setdefault(session_id, {"epoch": "", "fields": [], "commands": [], "command_sequence": 0})
        if live is not None:
            now = time.time()
            last_seen = runtime.get("receiver_seen", 0)
            if record["permission"] != "none" and now - last_seen > _RECEIVER_TIMEOUT:
                _revoke(record)
                runtime["commands"].clear()
                _event(record, "revoke", record["owner"], role="sender", reason="receiver_timeout")
                _save(session_id, record)
            if role == "receiver":
                runtime["receiver_seen"] = now
        if request.method == "POST":
            if live is None or record["closed"]:
                abort(410)
            changed = _mutate(record, runtime, body, role, actor)
            if changed:
                _save(session_id, record)
                screen.audit("screen_support_" + body["type"], "screen_session", session_id, detail={"event_sequence": record["sequence"]})
        elif live is not None and (original_receiver != record["receiver"] or not _path(session_id).exists()):
            _save(session_id, record)
        after, after_command = _cursor("after"), _cursor("after_command")
        commands = []
        if role == "sender":
            runtime["commands"] = [command for command in runtime["commands"] if command["seq"] > after_command]
            commands = runtime["commands"]
        return jsonify(schema=1, closed=record["closed"], state={key: record[key] for key in ("permission", "generation", "receiver")}, events=[event for event in record["events"] if event["seq"] > after], commands=commands, fields=runtime["fields"] if role == "receiver" and record["permission"] == "granted" else [], epoch=runtime["epoch"], last_sequence=record["sequence"], last_command_sequence=runtime["command_sequence"])


def register_support(bp):
    @bp.route("/api/sessions/<session_id>/support", methods=["GET", "POST"])
    @login_required
    def support(session_id):
        try:
            return _support_request(session_id)
        except (OSError, ValueError):
            current_app.logger.exception("Screen support storage unavailable")
            return jsonify(error="Support-Verlauf konnte nicht gespeichert oder gelesen werden.", code="support_storage_error"), 503
