"""Authenticated routes for dynamically discovered and activated Smart Views."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import re
import sqlite3
import time
from uuid import uuid4

from flask import session
from .documents_core import *  # noqa: F401,F403
from .smart_folder_batch_jobs import (
    BATCH_ACTIONS,
    MAX_BATCH_DOCUMENTS,
    MAX_BATCH_OCR_DOCUMENTS,
    MAX_BATCH_FILE_BYTES,
    MAX_BATCH_TOTAL_BYTES,
)
from .smart_folder_views import SmartFolderViews
from .safe_paths import resolve_file_under, resolve_under
from .v3_capabilities import enabled
from .v3_jobs import JobStore


def _smart_views() -> SmartFolderViews:
    return SmartFolderViews(_store(), str(g.user["username"]))


def _spreadsheet_safe_cell(value: object) -> str:
    text = "" if value is None else str(value)
    first = text.lstrip(" \t\r\n\x00")[:1]
    return "'" + text if first in {"=", "+", "-", "@"} else text


@bp.get("/smart-folders")
@login_required
def smart_folders():
    views = _smart_views()
    assignment_document = _assignment_document(request.args.get("assign_document_id", ""))
    move_document = _assignment_document(request.args.get("move_document_id", ""))
    if assignment_document and move_document:
        abort(400)
    return render_template(
        "documents/smart_folders.html",
        folders=views.folders(),
        selected=None,
        assignment_document=assignment_document,
        move_document=move_document,
    )


def _assignment_document(document_id: str) -> dict | None:
    document_id = str(document_id or "").strip()
    if not document_id:
        return None
    try:
        return _store().get_document(document_id)
    except ValueError:
        abort(404)


def _folder_tasks(views: SmartFolderViews, folder: str, actor: str) -> list[dict]:
    try:
        folder_ids = set(views.task_folder_ids(folder, actor))
    except ValueError:
        return []
    if not folder_ids:
        return []
    return [
        task for task in _todos().items(actor)
        if task.get("smart_folder_id") in folder_ids
    ]


def _task_fingerprint(tasks: list[dict]) -> str:
    snapshot = sorted(
        (
            str(task.get("id", "")),
            str(task.get("title", "")),
            str(task.get("status", "")),
            str(task.get("due", "")),
        )
        for task in tasks
    )
    return hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@bp.get("/smart-folders/view")
@login_required
def smart_folder_view():
    folder = request.args.get("folder", "").strip()
    if not folder:
        return redirect(url_for("documents.smart_folders"))
    views = _smart_views()
    assignment_document = _assignment_document(request.args.get("assign_document_id", ""))
    move_document = _assignment_document(request.args.get("move_document_id", ""))
    if assignment_document and move_document:
        abort(400)
    try:
        folders = views.folders()
        selected = views.view(folder, folders)
    except ValueError:
        abort(404)
    try:
        preview_index = int(request.args.get("preview_index", "0"))
    except (TypeError, ValueError):
        preview_index = 0
    preview_members = selected["preview_members"]
    selected["preview_index"] = (
        max(0, min(preview_index, len(preview_members) - 1))
        if preview_members else 0
    )
    selected["current_preview"] = (
        preview_members[selected["preview_index"]] if preview_members else None
    )
    rules = views.rules_for(selected["path"])
    try:
        rows = views.extract_rows(selected["path"]) if rules else []
    except (OSError, RuntimeError, ValueError) as exc:
        current_app.logger.warning("Smart folder extraction failed: %s", type(exc).__name__)
        rows = []
        flash("Die tabellarische Auswertung ist derzeit nicht verfügbar.")
    try:
        tasks = _folder_tasks(views, selected["path"], str(g.user["username"]))
    except (OSError, RuntimeError, ValueError) as exc:
        current_app.logger.warning("Smart folder tasks unavailable: %s", type(exc).__name__)
        tasks = []
        flash("Die Sammelordner-Aufgaben sind derzeit nicht verfügbar.")
    move_collision = False
    move_already_in_folder = False
    if move_document:
        move_already_in_folder = (
            Path(str(move_document.get("last_path", ""))).parent.as_posix()
            == selected["path"]
        )
        target_path = resolve_under(
            Path(current_app.config["DOCUMENT_ROOT"]),
            f"{selected['path']}/{Path(str(move_document.get('last_path', ''))).name}",
            strict=False,
        )
        move_collision = target_path.exists() and not move_already_in_folder
    return render_template(
        "documents/smart_folders.html",
        folders=folders,
        selected=selected,
        rules=rules,
        rows=rows,
        tasks=tasks,
        assignment_document=assignment_document,
        move_document=move_document,
        move_collision=move_collision,
        move_already_in_folder=move_already_in_folder,
        batch_jobs_enabled=enabled("v3.jobs"),
        batch_request_id=uuid4().hex,
    )


@bp.post("/smart-folders/activate")
@login_required
def activate_smart_folder():
    folder = request.form.get("folder", "")
    try:
        _smart_views().activate(folder, str(g.user["username"]), request.form.get("notes", ""))
        flash("Sammelordner aktiviert.")
    except ValueError as exc:
        flash(str(exc))
    except (OSError, RuntimeError) as exc:
        current_app.logger.warning("Smart folder activation failed: %s", type(exc).__name__)
        flash("Sammelordner konnte nicht gespeichert werden.")
    return redirect(url_for("documents.smart_folder_view", folder=folder))


@bp.post("/smart-folders/previews")
@login_required
def set_smart_folder_previews():
    folder = request.form.get("folder", "")
    try:
        _smart_views().set_preview_documents(
            folder,
            request.form.getlist("document_ids"),
            str(g.user["username"]),
        )
        flash("Sammelordner-Vorschau gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    except (OSError, RuntimeError) as exc:
        current_app.logger.warning("Smart folder preview selection failed: %s", type(exc).__name__)
        flash("Sammelordner-Vorschau konnte nicht gespeichert werden.")
    return redirect(url_for("documents.smart_folder_view", folder=folder))


@bp.post("/smart-folders/task")
@login_required
def create_smart_folder_task():
    folder = request.form.get("folder", "")
    actor = str(g.user["username"])
    try:
        folder_id = _smart_views().folder_task_id(folder, actor)
        _todos().add(
            request.form.get("title", ""),
            actor,
            {"description": request.form.get("description", "")},
            smart_folder_id=folder_id,
        )
        flash("Aufgabe mit dem Sammelordner verknüpft.")
    except ValueError as exc:
        flash(str(exc))
    except (OSError, RuntimeError) as exc:
        current_app.logger.warning("Smart folder task creation failed: %s", type(exc).__name__)
        flash("Aufgabe konnte nicht gespeichert werden.")
    return redirect(url_for("documents.smart_folder_view", folder=folder))


@bp.post("/smart-folders/batch")
@login_required
def enqueue_smart_folder_batch():
    folder = request.form.get("folder", "")
    actor = str(g.user["username"])
    if not enabled("v3.jobs"):
        flash("Die optionale Hintergrundwarteschlange ist nicht aktiviert.")
        return redirect(url_for("documents.smart_folder_view", folder=folder))
    try:
        views = _smart_views()
        folder_id = views.folder_task_id(folder, actor)
        selected = views.view(folder)
        action = str(request.form.get("action", ""))
        document_ids = [value.strip() for value in request.form.getlist("document_ids")]
        if action not in BATCH_ACTIONS:
            raise ValueError("Ungültige Sammelordner-Aktion")
        document_limit = (
            MAX_BATCH_OCR_DOCUMENTS
            if action in {"ocr", "preview_ocr"}
            else MAX_BATCH_DOCUMENTS
        )
        if (
            not document_ids
            or len(document_ids) > document_limit
            or any(not value for value in document_ids)
            or len(set(document_ids)) != len(document_ids)
        ):
            raise ValueError(
                f"Wähle höchstens {document_limit} unterschiedliche Dokumente aus"
            )
        members = {str(item["document_id"]): item for item in selected["members"]}
        if any(document_id not in members for document_id in document_ids):
            raise ValueError("Alle ausgewählten Dokumente müssen zum Sammelordner gehören")
        total_bytes = 0
        root = Path(current_app.config["DOCUMENT_ROOT"])
        for document_id in document_ids:
            metadata = _store().get_document(document_id)
            source = resolve_file_under(root, str(metadata.get("last_path", "")))
            size = source.stat().st_size
            if size > MAX_BATCH_FILE_BYTES:
                raise ValueError("Eine Datei überschreitet das Batch-Größenlimit")
            total_bytes += size
            if total_bytes > MAX_BATCH_TOTAL_BYTES:
                raise ValueError("Der Batch überschreitet das Gesamtgrößenlimit")
        request_id = str(request.form.get("batch_request_id", "")).strip()
        if not re.fullmatch(r"[0-9a-f]{32}", request_id):
            raise ValueError("Ungültige Batch-Anforderungs-ID")
        identity = "\0".join((actor, folder_id, request_id, action, *sorted(document_ids)))
        key = "smart-folder-batch:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
        job = JobStore(root).enqueue(
            "documents.smart_folder_batch",
            {
                "action": action,
                "folder": folder,
                "folder_id": folder_id,
                "document_ids": document_ids,
            },
            actor,
            idempotency_key=key,
            max_attempts=2,
            max_open_per_principal=2,
            max_open_total=500,
            max_created_per_principal_window=50,
            max_created_total_window=1000,
        )
        return redirect(url_for("documents.smart_folder_batch_job", job_id=job.job_id))
    except ValueError as exc:
        flash(str(exc))
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        current_app.logger.warning("Smart folder batch could not be queued: %s", type(exc).__name__)
        flash("Die Sammelordner-Aktion konnte nicht eingereiht werden.")
    return redirect(url_for("documents.smart_folder_view", folder=folder))


@bp.get("/smart-folders/jobs/<job_id>")
@login_required
def smart_folder_batch_job(job_id: str):
    try:
        job = JobStore(current_app.config["DOCUMENT_ROOT"]).get(job_id)
    except (OSError, sqlite3.Error) as exc:
        current_app.logger.warning("Smart folder job status unavailable: %s", type(exc).__name__)
        abort(503)
    if (
        job is None
        or job.principal != str(g.user["username"])
        or job.kind != "documents.smart_folder_batch"
    ):
        abort(404)
    return render_template("documents/smart_folder_job.html", job=job)


@bp.get("/smart-folders/merge-preview")
@login_required
def smart_folder_merge_preview():
    source = request.args.get("source", "").strip()
    target = request.args.get("target", "").strip()
    if not source or not target:
        return redirect(url_for("documents.smart_folders"))
    views = _smart_views()
    try:
        impact = views.merge_preview(source, target)
    except ValueError:
        abort(404)
    actor = str(g.user["username"])
    try:
        source_tasks = _folder_tasks(views, source, actor)
        target_tasks = _folder_tasks(views, target, actor)
        impact["source_tasks"] = len(source_tasks)
        impact["target_tasks"] = len(target_tasks)
        impact["source_task_rows"] = source_tasks[:200]
        impact["target_task_rows"] = target_tasks[:200]
        impact["source_tasks_truncated"] = len(source_tasks) > 200
        impact["target_tasks_truncated"] = len(target_tasks) > 200
        impact["task_preview_available"] = True
        session["smart_folder_merge_preview"] = {
            "source": source,
            "target": target,
            "fingerprint": impact["fingerprint"],
            "source_tasks": _task_fingerprint(source_tasks),
            "target_tasks": _task_fingerprint(target_tasks),
            "expires_at": int(time.time()) + 600,
        }
    except (OSError, RuntimeError, ValueError) as exc:
        current_app.logger.warning("Smart folder task merge preview failed: %s", type(exc).__name__)
        session.pop("smart_folder_merge_preview", None)
        impact["source_tasks"] = None
        impact["target_tasks"] = None
        impact["task_preview_available"] = False
    response = Response(
        render_template("documents/smart_folder_merge_preview.html", impact=impact),
        content_type="text/html; charset=utf-8",
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.post("/smart-folders/merge")
@login_required
def merge_smart_folders():
    source = request.form.get("source", "")
    target = request.form.get("target", "")
    if request.form.get("confirm") != "ZUSAMMENFUEHREN":
        flash("Fusion nicht bestätigt.")
        return redirect(url_for("documents.smart_folder_merge_preview", source=source, target=target))
    preview = session.get("smart_folder_merge_preview")
    fingerprint = str(request.form.get("preview_fingerprint", ""))
    if (
        not isinstance(preview, dict)
        or preview.get("source") != source
        or preview.get("target") != target
        or preview.get("fingerprint") != fingerprint
        or isinstance(preview.get("expires_at"), bool)
        or not isinstance(preview.get("expires_at"), int)
        or preview.get("expires_at", 0) < int(time.time())
    ):
        session.pop("smart_folder_merge_preview", None)
        flash("Die Fusionsvorschau fehlt oder ist abgelaufen. Bitte Auswirkungen erneut prüfen.")
        return redirect(url_for("documents.smart_folder_view", folder=source))
    try:
        views = _smart_views()
        actor = str(g.user["username"])
        source_tasks = _folder_tasks(views, source, actor)
        target_tasks = _folder_tasks(views, target, actor)
        if (
            _task_fingerprint(source_tasks) != preview.get("source_tasks")
            or _task_fingerprint(target_tasks) != preview.get("target_tasks")
        ):
            session.pop("smart_folder_merge_preview", None)
            flash("Aufgaben haben sich seit der Vorschau geändert. Bitte Auswirkungen erneut prüfen.")
            return redirect(url_for(
                "documents.smart_folder_merge_preview", source=source, target=target,
            ))
        views.merge_assignments(
            source, target, actor, expected_fingerprint=fingerprint,
        )
        session.pop("smart_folder_merge_preview", None)
        flash("Logische Zuordnungen und Regeln übernommen. Originaldateien wurden nicht verschoben.")
        return redirect(url_for("documents.smart_folder_view", folder=target))
    except ValueError as exc:
        session.pop("smart_folder_merge_preview", None)
        flash(str(exc))
        return redirect(url_for("documents.smart_folders"))
    except (OSError, RuntimeError) as exc:
        session.pop("smart_folder_merge_preview", None)
        current_app.logger.warning("Smart folder merge failed: %s", type(exc).__name__)
        flash("Ordner konnten nicht zusammengeführt werden.")
        return redirect(url_for("documents.smart_folders"))


@bp.post("/smart-folders/rule")
@login_required
def save_smart_folder_rule():
    folder = request.form.get("folder", "")
    try:
        _smart_views().save_rule(
            folder,
            request.form.get("name", ""),
            request.form.get("field", ""),
            str(g.user["username"]),
        )
        flash("Extraktionsregel gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    except (OSError, RuntimeError) as exc:
        current_app.logger.warning("Smart folder rule save failed: %s", type(exc).__name__)
        flash("Extraktionsregel konnte nicht gespeichert werden.")
    return redirect(url_for("documents.smart_folder_view", folder=folder))


@bp.get("/smart-folders/export.csv")
@login_required
def export_smart_folder():
    folder = request.args.get("folder", "").strip()
    if not folder:
        return redirect(url_for("documents.smart_folders"))
    try:
        rows = _smart_views().extract_rows(folder)
    except ValueError:
        abort(404)
    except (OSError, RuntimeError) as exc:
        current_app.logger.warning("Smart folder export failed: %s", type(exc).__name__)
        return Response(
            "Die tabellarische Auswertung ist derzeit nicht verfügbar.\n",
            status=503,
            content_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )
    columns = list(dict.fromkeys(key for row in rows for key in row))
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow([_spreadsheet_safe_cell(column) for column in columns])
    for row in rows:
        writer.writerow([_spreadsheet_safe_cell(row.get(column, "")) for column in columns])
    response = Response(
        stream.getvalue(),
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="smart-folder.csv"'},
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.post("/<document_id>/smart-folder")
@login_required
def assign_smart_folder(document_id: str):
    folder = request.form.get("folder", "")
    try:
        _smart_views().assign(document_id, folder)
        flash("Dokument wurde der Smart View zugeordnet. Der Originalspeicherort bleibt erhalten.")
    except ValueError as exc:
        flash(str(exc))
    except (OSError, RuntimeError) as exc:
        current_app.logger.warning("Smart folder assignment failed: %s", type(exc).__name__)
        flash("Dokument konnte der Smart View nicht zugeordnet werden.")
    return redirect(url_for("documents.detail", document_id=document_id))
