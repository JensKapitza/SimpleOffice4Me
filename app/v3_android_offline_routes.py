"""Feature-gated Android offline workset UI and synchronization API."""
from __future__ import annotations

import json
from typing import Any

from flask import Blueprint, abort, current_app, g, jsonify, make_response, render_template, request

from .access_control import audit, has_feature
from .auth import login_required
from .todo_store import TodoStore
from .v3_capabilities import enabled


bp = Blueprint("v3_android_offline", __name__)

MAX_SYNC_OPERATIONS = 64
MAX_OPERATION_PAYLOAD = 16_384
ALLOWED_TASK_STATUSES = {"needs-action", "in-process", "completed", "cancelled"}


@bp.app_context_processor
def android_offline_capability_context():
    return {"v3_android_offline_enabled": enabled("v3.android_offline")}


def _require_enabled() -> None:
    if not enabled("v3.android_offline"):
        abort(404)


def _require_tasks() -> None:
    if not has_feature(g.user, "projects"):
        abort(403)


def _actor() -> str:
    return str(g.user["username"])


def _store() -> TodoStore:
    return TodoStore(current_app.config["DOCUMENT_ROOT"])


def _safe_task(item: dict[str, Any], store: TodoStore) -> dict[str, Any]:
    return {
        "id": str(item.get("id", "")),
        "title": str(item.get("title", "")),
        "description": str(item.get("description", "")),
        "status": str(item.get("status", "needs-action")),
        "percent_complete": int(item.get("percent_complete", 0) or 0),
        "priority": int(item.get("priority", 0) or 0),
        "start": str(item.get("start", "")),
        "due": str(item.get("due", "")),
        "project_id": str(item.get("project_id", "")),
        "list_id": str(item.get("list_id", "")),
        "updated_at": str(item.get("updated_at", "")),
        "version": store.etag(item),
    }


def _visible_task(task_id: str) -> tuple[dict[str, Any], TodoStore]:
    store = _store()
    item = next(
        (row for row in store.items(_actor()) if str(row.get("id", "")) == str(task_id)),
        None,
    )
    if item is None:
        abort(404)
    return item, store


def _payload_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        payload = value
    elif isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_OPERATION_PAYLOAD:
            raise ValueError("offline operation payload is too large")
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("offline operation payload is invalid") from exc
    else:
        raise ValueError("offline operation payload is invalid")
    if not isinstance(payload, dict):
        raise ValueError("offline operation payload is invalid")
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_OPERATION_PAYLOAD:
        raise ValueError("offline operation payload is too large")
    return payload


@bp.get("/android/offline")
@login_required
def index():
    _require_enabled()
    _require_tasks()
    store = _store()
    tasks = [_safe_task(row, store) for row in store.items(_actor())]
    return render_template("android/offline.html", enabled=True, tasks=tasks)


@bp.get("/api/v3/android-offline/policy")
@login_required
def policy():
    _require_enabled()
    _require_tasks()
    return jsonify({
        "offline_allowed": ["task"],
        "online_only": ["calendar", "contacts", "mail", "federation"],
        "never_local": [
            "password",
            "session_secret",
            "oauth_token",
            "api_key",
            "federation_private_key",
            "vault_secret",
        ],
        "mutation_types": ["task_status"],
        "limits": {
            "max_items": 128,
            "max_total_bytes": 128 * 1024 * 1024,
            "max_item_bytes": 16 * 1024 * 1024,
            "max_outbox": MAX_SYNC_OPERATIONS,
            "max_retention_seconds": 30 * 24 * 60 * 60,
        },
    })


@bp.get("/api/v3/android-offline/tasks")
@login_required
def tasks_api():
    _require_enabled()
    _require_tasks()
    store = _store()
    return jsonify({"tasks": [_safe_task(row, store) for row in store.items(_actor())]})


@bp.get("/api/v3/android-offline/tasks/<task_id>")
@login_required
def task_api(task_id: str):
    _require_enabled()
    _require_tasks()
    item, store = _visible_task(task_id)
    payload = _safe_task(item, store)
    response = make_response(jsonify(payload))
    response.headers["ETag"] = payload["version"]
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/api/v3/android-offline/sync")
@login_required
def sync():
    _require_enabled()
    _require_tasks()
    if request.content_length is not None and request.content_length > 1024 * 1024:
        return jsonify({"error": "offline synchronization request is too large"}), 413
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("operations"), list):
        return jsonify({"error": "operations must be a JSON array"}), 400
    operations = data["operations"]
    if len(operations) > MAX_SYNC_OPERATIONS:
        return jsonify({"error": "too many offline operations"}), 413

    store = _store()
    actor = _actor()
    results: list[dict[str, Any]] = []
    for raw in operations:
        operation_id = ""
        target_id = ""
        base_version = ""
        try:
            if not isinstance(raw, dict):
                raise ValueError("offline operation must be an object")
            operation_id = str(raw.get("operationId", "")).strip()
            target_id = str(raw.get("targetId", "")).strip()
            base_version = str(raw.get("baseVersion", "")).strip()
            mutation_type = str(raw.get("mutationType", "")).strip()
            if mutation_type != "task_status":
                raise ValueError("unsupported offline mutation")
            payload = _payload_object(raw.get("payload", {}))
            if set(payload) - {"status"}:
                raise ValueError("unsupported task status payload")
            status = str(payload.get("status", "")).strip().lower()
            if status not in ALLOWED_TASK_STATUSES:
                raise ValueError("invalid task status")
            result = store.apply_offline_status(
                target_id,
                status,
                actor,
                expected_etag=base_version,
                operation_id=operation_id,
            )
            audit(
                "android_offline_sync",
                "todo",
                target_id,
                outcome=str(result.get("status", "success")),
                detail={
                    "operation_id": operation_id,
                    "base_version": base_version,
                    "server_version": str(result.get("serverVersion", "")),
                    "replayed": bool(result.get("replayed", False)),
                },
            )
            results.append(result)
        except ValueError:
            result = {
                "status": "rejected",
                "operationId": operation_id,
                "targetId": target_id,
                "baseVersion": base_version,
            }
            audit(
                "android_offline_sync",
                "todo",
                target_id,
                outcome="rejected",
                detail={"operation_id": operation_id, "base_version": base_version},
            )
            results.append(result)
    return jsonify({"results": results})
