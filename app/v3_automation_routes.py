"""Browser UI for declarative V3 automation rules."""
from __future__ import annotations

import json
import hashlib
import re
import secrets
import sqlite3
from flask import Blueprint, abort, current_app, flash, g, jsonify, make_response, redirect, render_template, request, url_for

from .access_control import has_feature
from .auth import login_required
from .v3_activity import ActivityStore
from .v3_automation import AutomationEngine, AutomationStore, default_action_registry
from .v3_capabilities import enabled
from .workflow_peer_notices import DEST, PEER_NOTICE, PeerNoticeGrants, feature_on, permitted_peer
from .federation_store import FederationStore
from . import workflow_peer_notice_jobs as peer_jobs
from .v3_copy_notices import (
    COPY_EVENT, JOB_EVENT_NAME, JOB_NOTICE, NOTICE_EVENTS, PRESET, TIMER_PRESETS, enable_copy_notice_preset,
    is_local_copy_notice_rule, recent_copy_notices,
)


bp = Blueprint("v3_automation", __name__, url_prefix="/automation-v3")


@bp.app_context_processor
def _automation_navigation():
    user = getattr(g, "user", None)
    return {"v3_automation_nav": bool(user is not None and enabled("v3.automation"))}


@bp.after_request
def _private_response(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.vary.add("Cookie")
    return response


def _store() -> AutomationStore:
    return AutomationStore(current_app.config["DOCUMENT_ROOT"])


def _values() -> dict:
    try:
        trigger = json.loads(request.form.get("trigger_json", "{}"))
        conditions = json.loads(request.form.get("conditions_json", "[]"))
        actions = json.loads(request.form.get("actions_json", "[]"))
    except json.JSONDecodeError as exc:
        raise ValueError("JSON ist ungültig") from exc
    return {
        "name": request.form.get("name", ""),
        "enabled": request.form.get("enabled") == "1",
        "scope": {},
        "trigger": trigger,
        "conditions": conditions,
        "actions": actions,
    }


def _task_check_rule(rule) -> bool:
    if not (
        rule.enabled
        and str(rule.trigger.get("type", "")) == "check"
        and len(rule.actions) == 1
        and str(rule.actions[0].get("type", "")) == "task.create"
    ):
        return False
    payload = rule.actions[0].get("payload", {})
    if not isinstance(payload, dict) or set(payload) - {"title", "values"}:
        return False
    title = payload.get("title")
    values = payload.get("values", {})
    if not isinstance(title, str) or not title.strip() or len(title) > 300:
        return False
    if not isinstance(values, dict) or set(values) - {"description", "due", "priority", "list_id"}:
        return False
    if "description" in values and (not isinstance(values["description"], str) or len(values["description"]) > 10000):
        return False
    if "due" in values and (not isinstance(values["due"], str) or len(values["due"]) > 200):
        return False
    if "priority" in values and (type(values["priority"]) is not int or not 0 <= values["priority"] <= 9):
        return False
    if "list_id" in values and (
        not isinstance(values["list_id"], str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", values["list_id"])
    ):
        return False
    return True


def _peer_notice_options():
    root = current_app.config["DOCUMENT_ROOT"]
    if not feature_on(root) or not has_feature(g.user, "sync"):
        return None
    peers = {"receive": [], "send": []}
    for row in FederationStore(root).list_peers():
        for direction, items in peers.items():
            try:
                peer = permitted_peer(root, row["peer_id"], direction)
                items.append({"peer_id": peer["peer_id"], "label": peer["label"]})
            except (ValueError, PermissionError):
                continue
    return {"peers": peers["receive"], "send": peers["send"] if peer_jobs.available(root) else [],
            "grants": PeerNoticeGrants(root).for_account(g.user),
            "delays": peer_jobs.DELAYS, "deadlines": peer_jobs.DEADLINES,
            "zone": g.app_settings.get("interface", {}).get("timezone", "UTC")}


def _notice_inbox(actor, *, strict=False):
    root = current_app.config["DOCUMENT_ROOT"]
    try:
        rows = recent_copy_notices(root, actor, strict=strict)
        if not rows:
            return [], set()
        read = ActivityStore(root).notice_read_ids(g.user["id"], (row.event_id for row in rows))
        return rows, read
    except (OSError, sqlite3.Error):
        if strict:
            raise
        return [], set()


def _render_index(actor: str, *, preview=None):
    store = _store()
    rules = store.list(actor)
    manual_check_rule_ids = (
        {rule.rule_id for rule in rules if _task_check_rule(rule)}
        if has_feature(g.user, "projects") else set()
    )
    copy_notice_delays = {
        source: next((
            int(rule.actions[0].get("timing", {}).get("delay_seconds", 0))
            for rule in rules if rule.enabled and rule.scope == PRESET and is_local_copy_notice_rule(rule, event)
        ), None)
        for source, event in (("browser", COPY_EVENT), ("job", JOB_EVENT_NAME))
    }
    try:
        peer_receipts = peer_jobs.receipts(current_app.config["DOCUMENT_ROOT"], actor, store, rules)
    except (OSError, sqlite3.Error, ValueError) as exc:
        current_app.logger.warning("Could not read copy delivery receipts: %s", type(exc).__name__)
        peer_receipts = None
    rows, seen = _notice_inbox(actor)
    return render_template(
        "admin/v3_automation.html",
        enabled=True,
        rules=rules,
        logs=store.logs(actor, limit=50),
        preview=preview,
        copy_notices_enabled=enabled("v3.activity"),
        copy_notice_timer_enabled=enabled("v3.jobs"),
        copy_notice_delays=copy_notice_delays,
        copy_job_notice_type=JOB_NOTICE,
        peer_notice_type=PEER_NOTICE,
        peer_notice_options=_peer_notice_options(),
        peer_receipts=peer_receipts,
        copy_notices=rows,
        read_notices=seen,
        manual_check_rule_ids=manual_check_rule_ids,
    )


@bp.get("")
@login_required
def index():
    actor = str(g.user["username"])
    if not enabled("v3.automation"):
        return render_template("admin/v3_automation.html", enabled=False, rules=[], logs=[])
    return _render_index(actor)


@bp.get("/copy-notices")
@login_required
def copy_notices_feed():
    if not enabled("v3.automation") or not enabled("v3.activity"):
        return jsonify({"enabled": False, "notices": []})
    try:
        rows, seen = _notice_inbox(str(g.user["username"]), strict=True)
    except (OSError, sqlite3.Error):
        abort(503)
    return jsonify({"enabled": True, **({"seen": sorted(seen)} if seen else {}), "notices": [
        {"id": row.event_id, "kind": {JOB_NOTICE: "job", PEER_NOTICE: "peer"}.get(row.event_type, "browser"),
         "at": row.occurred_at}
        for row in rows
    ]})


@bp.post("/copy-notices/read")
@login_required
def mark_copy_notices_read():
    if not enabled("v3.automation") or not enabled("v3.activity"):
        abort(404)
    if not request.is_json or not request.content_length or request.headers.get("Transfer-Encoding"):
        abort(400)
    if request.content_length > 1024:
        abort(413)
    body = request.get_json(silent=True)
    if type(body) is not dict or set(body) != {"ids"} or type(body["ids"]) is not list:
        abort(400)
    ids = body["ids"]
    if not 1 <= len(ids) <= 10 or not all(isinstance(i, str) and re.fullmatch(r"[a-f0-9]{64}", i) for i in ids):
        abort(400)
    try:
        ActivityStore(current_app.config["DOCUMENT_ROOT"]).mark_copy_notices_read(
            g.user["id"], str(g.user["username"]), ids, (*NOTICE_EVENTS.values(), PEER_NOTICE),
        )
    except LookupError:
        abort(404)
    except (OSError, sqlite3.Error):
        abort(503)
    return "", 204


@bp.post("/peer-notices")
@login_required
def peer_notices_consent():
    root = current_app.config["DOCUMENT_ROOT"]
    if not feature_on(root):
        abort(404)
    grants = PeerNoticeGrants(root)
    if request.form.get("operation") == "grant":
        if not has_feature(g.user, "sync"):
            abort(403)
        try:
            peer = permitted_peer(root, request.form.get("peer", ""), "receive")
        except (ValueError, PermissionError):
            abort(403)
        grants.grant(peer["peer_id"], g.user)
    elif request.form.get("operation") == "revoke":
        grant = request.form.get("grant", "")
        if not DEST.fullmatch(grant):
            abort(400)
        grants.revoke(grant, g.user)
    else:
        abort(400)
    return redirect(url_for("v3_automation.index"), code=303)


@bp.post("/peer-copy-rule")
@login_required
def create_peer_copy_rule():
    root = current_app.config["DOCUMENT_ROOT"]
    if not peer_jobs.available(root):
        abort(404)
    if not has_feature(g.user, "sync"):
        abort(403)
    source = request.form.get("source")
    event_type = {"browser": COPY_EVENT, "job": JOB_EVENT_NAME}.get(source)
    language = getattr(g, "language", "")
    kind = (("Copy job" if source == "job" else "Document copy") if language == "en"
            else ("Kopierjob" if source == "job" else "Dokumentkopie"))
    try:
        peer = permitted_peer(root, request.form.get("peer", ""), "send")
        delay, deadline = request.form.get("delay", ""), request.form.get("deadline", "")
        if (delay not in set(map(str, peer_jobs.DELAYS))
                or deadline not in {*map(str, peer_jobs.DEADLINES), peer_jobs.DAY_END}):
            raise ValueError("invalid time window")
        peer_jobs.save_rule(
            root, str(g.user["username"]), peer["peer_id"], request.form.get("destination", ""), event_type,
            delay=int(delay), deadline=peer_jobs.DAY_END if deadline == peer_jobs.DAY_END else int(deadline),
            zone=request.form.get("zone", ""),
            name=f"{kind} → {peer['label']}",
        )
    except PermissionError:
        abort(403)
    except ValueError:
        flash("Invalid copy notification settings." if language == "en" else "Ungültige Einstellungen für Kopiermeldungen.")
    except (OSError, sqlite3.Error) as exc:
        current_app.logger.warning("Could not save peer copy rule: %s", type(exc).__name__)
        abort(503)
    else:
        flash("Peer copy notifications enabled." if language == "en" else "Kopiermeldungen an die Instanz aktiviert.")
    return redirect(url_for("v3_automation.index"), code=303)


@bp.post("/copy-notice")
@login_required
def create_copy_notice():
    if not enabled("v3.automation") or not enabled("v3.activity"):
        abort(404)
    actor = str(g.user["username"])
    language = getattr(g, "language", "")
    source = request.form.get("source", "browser")
    item = {
        "browser": (COPY_EVENT, "Document copied", "Dokument kopiert"),
        "job": (JOB_EVENT_NAME, "Copy job completed", "Kopierjob abgeschlossen"),
    }.get(source)
    delay_value = request.form.get("delay", "0")
    if item is None or delay_value not in {str(value) for value in TIMER_PRESETS}:
        abort(400)
    delay = int(delay_value)
    if delay and not enabled("v3.jobs"):
        abort(409)
    event, name_en, name_de = item
    try:
        enable_copy_notice_preset(_store(), actor, name_en if language == "en" else name_de, event, delay=delay)
        flash("Local copy notifications enabled." if language == "en" else "Lokale Kopiermeldungen aktiviert.")
    except (OSError, sqlite3.Error) as exc:
        current_app.logger.warning("Could not save copy notification preset: %s", type(exc).__name__)
        flash("Could not save notifications." if language == "en" else "Kopiermeldungen konnten nicht gespeichert werden.")
    return redirect(url_for("v3_automation.index"))


@bp.post("")
@login_required
def create():
    if not enabled("v3.automation"):
        abort(404)
    actor = str(g.user["username"])
    try:
        _store().create(actor, _values())
        flash("Automation gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("v3_automation.index"))


@bp.post("/<rule_id>/toggle")
@login_required
def toggle(rule_id):
    if not enabled("v3.automation"):
        abort(404)
    actor = str(g.user["username"])
    store = _store()
    try:
        rule = store.get(rule_id, actor)
        store.update(rule_id, actor, {"enabled": not rule.enabled})
    except (LookupError, ValueError):
        abort(404)
    return redirect(url_for("v3_automation.index"))


@bp.post("/<rule_id>/dry-run")
@login_required
def dry_run(rule_id):
    if not enabled("v3.automation"):
        abort(404)
    actor = str(g.user["username"])
    store = _store()
    try:
        rule = store.get(rule_id, actor)
        event = json.loads(request.form.get("event_json", "{}"))
        if not isinstance(event, dict):
            raise ValueError("event must be an object")
        result = AutomationEngine(
            store, default_action_registry(current_app.config["DOCUMENT_ROOT"])
        ).execute(rule, event, principal=actor, trigger_type=str(rule.trigger.get("type")), dry_run=True)
        flash(f"Dry-Run: {result.status}; Aktionen: {len(result.actions)}")
    except (LookupError, ValueError, json.JSONDecodeError) as exc:
        flash(str(exc))
    return redirect(url_for("v3_automation.index"))


@bp.post("/<rule_id>/prepare-check-run")
@login_required
def prepare_check_run(rule_id):
    if not enabled("v3.automation"):
        abort(404)
    if not has_feature(g.user, "projects"):
        abort(403)
    actor = str(g.user["username"])
    store = _store()
    try:
        rule = store.get(rule_id, actor)
        if not _task_check_rule(rule):
            abort(409)
        raw_event = request.form.get("event_json", "{}")
        if len(raw_event.encode("utf-8")) > 64 * 1024:
            raise ValueError("check context is too large")
        event = json.loads(raw_event)
        if not isinstance(event, dict):
            raise ValueError("check context must be an object")
        result = AutomationEngine(
            store, default_action_registry(current_app.config["DOCUMENT_ROOT"])
        ).execute(
            rule,
            event,
            principal=actor,
            trigger_type="check",
            dry_run=True,
        )
        if result.status != "dry_run":
            flash(f"Preview: {result.status}; no task was created.")
            return redirect(url_for("v3_automation.index"))
        token = secrets.token_urlsafe(32)
        expires_at = store.create_confirmation(rule, event, principal=actor, token=token)
        response = make_response(_render_index(actor, preview={
            "token": token,
            "rule": rule,
            "event": event,
            "actions": result.actions,
            "expires_at": expires_at,
        }))
        response.cache_control.private = True
        response.cache_control.no_store = True
        return response
    except LookupError:
        abort(404)
    except (ValueError, json.JSONDecodeError, RecursionError) as exc:
        flash(str(exc))
        return redirect(url_for("v3_automation.index"))


@bp.post("/<rule_id>/confirm-check-run")
@login_required
def confirm_check_run(rule_id):
    if not enabled("v3.automation"):
        abort(404)
    if not has_feature(g.user, "projects"):
        abort(403)
    if request.form.get("confirm_run") != "1":
        abort(400)
    actor = str(g.user["username"])
    token = request.form.get("run_token", "")
    if not token or len(token) > 128 or not re.fullmatch(r"[0-9a-f]{32}", rule_id):
        abort(400)
    store = _store()
    with store.rule_lock(rule_id):
        confirmation = store.get_confirmation(token, actor)
        if confirmation is None or confirmation["rule_id"] != rule_id:
            abort(404)
        try:
            rule = store.get(rule_id, actor)
        except LookupError:
            abort(404)
        if (
            not _task_check_rule(rule)
            or rule.version != confirmation["rule_version"]
            or confirmation["principal"] != actor
        ):
            flash("Rule changed or is no longer active. Preview it again before running.")
            return redirect(url_for("v3_automation.index"))
        event_json = json.dumps(confirmation["event"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        event_fingerprint = hashlib.sha256(event_json.encode("utf-8")).hexdigest()
        result = AutomationEngine(
            store, default_action_registry(current_app.config["DOCUMENT_ROOT"])
        ).execute(
            rule,
            confirmation["event"],
            principal=actor,
            trigger_type="check",
            correlation_id=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            idempotency_fingerprint=event_fingerprint,
        )
    flash(f"Run: {result.status}; actions: {len(result.actions)}")
    return redirect(url_for("v3_automation.index"))
