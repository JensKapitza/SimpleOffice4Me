"""Browser UI for declarative V3 automation rules."""
from __future__ import annotations

import json
import hashlib
import re
import secrets
import sqlite3
from flask import Blueprint, abort, current_app, flash, g, make_response, redirect, render_template, request, url_for

from .access_control import has_feature
from .auth import login_required
from .v3_automation import AutomationEngine, AutomationStore, default_action_registry
from .v3_capabilities import enabled
from .v3_copy_notices import PRESET, enable_copy_notice_preset, is_local_copy_notice_rule, recent_copy_notices


bp = Blueprint("v3_automation", __name__, url_prefix="/automation-v3")


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


def _render_index(actor: str, *, preview=None):
    store = _store()
    rules = store.list(actor)
    manual_check_rule_ids = (
        {rule.rule_id for rule in rules if _task_check_rule(rule)}
        if has_feature(g.user, "projects") else set()
    )
    return render_template(
        "admin/v3_automation.html",
        enabled=True,
        rules=rules,
        logs=store.logs(actor, limit=50),
        preview=preview,
        copy_notices_enabled=enabled("v3.activity"),
        copy_notice_preset_active=any(
            rule.enabled and rule.scope == PRESET and is_local_copy_notice_rule(rule) for rule in rules
        ),
        copy_notices=recent_copy_notices(current_app.config["DOCUMENT_ROOT"], actor),
        manual_check_rule_ids=manual_check_rule_ids,
    )


@bp.get("")
@login_required
def index():
    actor = str(g.user["username"])
    if not enabled("v3.automation"):
        return render_template("admin/v3_automation.html", enabled=False, rules=[], logs=[])
    return _render_index(actor)


@bp.post("/copy-notice")
@login_required
def create_copy_notice():
    if not enabled("v3.automation") or not enabled("v3.activity"):
        abort(404)
    actor = str(g.user["username"])
    language = getattr(g, "language", "")
    try:
        enable_copy_notice_preset(_store(), actor, "Document copied" if language == "en" else "Dokument kopiert")
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
