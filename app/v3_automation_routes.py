"""Browser UI for declarative V3 automation rules."""
from __future__ import annotations

import json
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .auth import login_required
from .v3_automation import AutomationEngine, AutomationStore, default_action_registry
from .v3_capabilities import enabled


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


@bp.get("")
@login_required
def index():
    actor = str(g.user["username"])
    if not enabled("v3.automation"):
        return render_template("admin/v3_automation.html", enabled=False, rules=[], logs=[])
    store = _store()
    return render_template(
        "admin/v3_automation.html",
        enabled=True,
        rules=store.list(actor),
        logs=store.logs(actor, limit=50),
    )


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
